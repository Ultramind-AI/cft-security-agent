from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from api.registry import ApiTargetRegistry, RegisteredTarget
from api.scheduler import ResourceBudget, ResourceRequest, RunScheduler
from api.store import ApiStore
from app.config import settings
from pipeline.cancellation import CancellationToken, RunCancelled, cancellation_scope
from pipeline.errors import error_from_exception
from schemas.api import (
    ApiEvidence,
    ApiFinding,
    ApiProject,
    ApiRun,
    CreateRunRequest,
    FindingTimeline,
    RunTimeline,
)
from schemas.pipeline import GateResult
from schemas.report import FinalReport

PipelineRunner = Callable[[argparse.Namespace], int]


class RunNotReadyError(RuntimeError):
    pass


class RunOrchestrator:
    """Bounded background orchestration over the canonical CI pipeline."""

    def __init__(
        self,
        *,
        registry: ApiTargetRegistry,
        store: ApiStore,
        artifact_root: str | Path,
        pipeline_runner: PipelineRunner | None = None,
        scheduler: RunScheduler | None = None,
        resource_budget: ResourceBudget | None = None,
        resource_request: ResourceRequest | None = None,
        max_workers: int | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.artifact_root = Path(artifact_root).expanduser().resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.pipeline_runner = pipeline_runner or _default_pipeline_runner
        self._scheduler = scheduler or RunScheduler(
            workers=max_workers or settings.api_max_concurrent_runs,
        )
        self._owns_scheduler = scheduler is None
        self._budget = resource_budget or ResourceBudget(
            sandboxes=settings.api_max_concurrent_sandboxes,
            cpu=settings.api_total_cpu_budget,
            memory_mb=settings.api_total_memory_mb,
        )
        self._request = resource_request or ResourceRequest(
            sandboxes=1,
            cpu=settings.api_run_cpu,
            memory_mb=settings.api_run_memory_mb,
        )
        self._closed = False

        for target in self.registry.list():
            self.store.upsert_project(
                profile_path=target.profile_path,
                profile=target.profile,
            )
        self.store.recover_abandoned()
        self._restore_queued_runs()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for run in self.store.list_runs(limit=500):
            if run.status in {"queued", "running", "cancelling"}:
                self.cancel_run(run.id, reason="API service is shutting down")
        if self._owns_scheduler:
            self._scheduler.close(wait=True)

    def list_projects(self) -> list[ApiProject]:
        return self.store.list_projects()

    def create_run(self, request: CreateRunRequest) -> ApiRun:
        if self._closed:
            raise RuntimeError("Run orchestrator is closed")
        target = self.registry.get(request.target_id)
        repository = target.profile.repository_path
        if repository is None:
            raise ValueError("Registered TargetProfile has no repository_path")
        if not repository.is_dir():
            raise ValueError("Registered target repository is not available")

        run_id = f"run-{uuid4().hex}"
        artifact_dir = self.artifact_root / run_id
        run = self.store.create_run(
            run_id=run_id,
            target_id=request.target_id,
            agent_mode=request.agent_mode,
            max_iterations=request.max_iterations,
            artifact_dir=artifact_dir,
        )
        self._schedule(run_id, target, request, artifact_dir)
        return run

    def cancel_run(self, run_id: str, *, reason: str = "Cancelled by operator") -> ApiRun:
        run = self.store.request_cancel(run_id, reason)
        if run.status in {"cancelling", "cancelled"}:
            self._scheduler.cancel(run_id)
            self._budget.wake()
        return self.store.get_run(run_id)

    def get_run(self, run_id: str) -> ApiRun:
        return self.store.get_run(run_id)

    def list_runs(self, *, limit: int = 100) -> list[ApiRun]:
        return self.store.list_runs(limit=limit)

    def list_findings(self, run_id: str) -> list[ApiFinding]:
        return self.store.list_findings(run_id)

    def get_finding(self, run_id: str, finding_id: str) -> ApiFinding:
        return self.store.get_finding(run_id, finding_id)

    def list_evidence(
        self,
        run_id: str,
        *,
        finding_id: str | None = None,
    ) -> list[ApiEvidence]:
        return self.store.list_evidence(run_id, finding_id=finding_id)

    def get_report(self, run_id: str, finding_id: str) -> FinalReport:
        self._require_completed(run_id)
        path = self.store.report_path(run_id, finding_id)
        return FinalReport.model_validate_json(path.read_text(encoding="utf-8"))

    def list_reports(self, run_id: str) -> list[FinalReport]:
        self._require_completed(run_id)
        return [
            self.get_report(run_id, finding.finding_id)
            for finding in self.store.list_findings(run_id)
        ]

    def get_gate(self, run_id: str) -> GateResult:
        self._require_completed(run_id)
        path = self.store.artifact_dir(run_id) / "gate.json"
        if not path.is_file():
            raise FileNotFoundError("Run gate artifact is missing")
        return GateResult.model_validate_json(path.read_text(encoding="utf-8"))

    def get_timeline(self, run_id: str) -> RunTimeline:
        reports = self.list_reports(run_id)
        return RunTimeline(
            run_id=run_id,
            findings=[
                FindingTimeline(
                    finding_id=report.finding_id,
                    agent_decisions=report.agent_decisions,
                    sandbox_actions=report.sandbox_actions,
                )
                for report in reports
            ],
        )

    def _execute_run(
        self,
        run_id: str,
        target: RegisteredTarget,
        request: CreateRunRequest,
        artifact_dir: Path,
        token: CancellationToken,
    ) -> None:
        lease = None
        cancelled = False
        try:
            with cancellation_scope(token):
                lease = self._budget.acquire(self._request, token)
                token.raise_if_cancelled()
                if not self.store.mark_running(run_id):
                    return
                args = _pipeline_args(
                    target=target,
                    request=request,
                    artifact_dir=artifact_dir,
                    cancellation_token=token,
                )
                exit_code = self.pipeline_runner(args)
                token.raise_if_cancelled()
                gate = _read_gate(artifact_dir)
                reports = _read_reports(artifact_dir)
                self.store.replace_results(run_id, reports=reports)
                error = gate.errors[0] if exit_code == 2 and gate.errors else None
                if not self.store.mark_finished(
                    run_id,
                    exit_code=exit_code,
                    gate_decision=gate.decision,
                    error=error,
                ):
                    cancelled = self.store.get_run(run_id).status == "cancelling"
        except RunCancelled:
            cancelled = True
        except Exception as exc:  # noqa: BLE001 - background errors are persisted
            error = error_from_exception(
                exc,
                layer="pipeline",
                public_message="API orchestration failed",
            )
            self.store.mark_failed(run_id, error)
        finally:
            if lease is not None:
                lease.release()
        if cancelled:
            # Статус cancelled ставится только после teardown pipeline и возврата ресурсов.
            self.store.mark_cancelled(run_id)

    def _require_completed(self, run_id: str) -> ApiRun:
        run = self.store.get_run(run_id)
        if run.status in {"queued", "running", "cancelling", "cancelled"}:
            raise RunNotReadyError("Run has no completed report artifacts")
        return run

    def _schedule(
        self,
        run_id: str,
        target: RegisteredTarget,
        request: CreateRunRequest,
        artifact_dir: Path,
    ) -> None:
        self._scheduler.submit(
            run_id,
            lambda token: self._execute_run(
                run_id,
                target,
                request,
                artifact_dir,
                token,
            ),
        )

    def _restore_queued_runs(self) -> None:
        for job in self.store.queued_jobs():
            try:
                target = self.registry.get(job.target_id)
                repository = target.profile.repository_path
                if repository is None or not repository.is_dir():
                    raise ValueError("Registered target repository is unavailable")
                self._schedule(
                    job.run_id,
                    target,
                    CreateRunRequest(
                        target_id=job.target_id,
                        agent_mode=job.agent_mode,
                        max_iterations=job.max_iterations,
                    ),
                    job.artifact_dir,
                )
            except (KeyError, RuntimeError, ValueError) as exc:
                error = error_from_exception(
                    exc,
                    layer="pipeline",
                    public_message="Queued run could not be restored",
                )
                self.store.mark_failed(job.run_id, error)


def _pipeline_args(
    *,
    target: RegisteredTarget,
    request: CreateRunRequest,
    artifact_dir: Path,
    cancellation_token: CancellationToken,
) -> argparse.Namespace:
    profile = target.profile
    assert profile.repository_path is not None
    return argparse.Namespace(
        target=str(profile.repository_path),
        target_repository=None,
        profile=str(target.profile_path),
        architecture=(str(profile.architecture.file) if profile.architecture.file else None),
        architecture_overrides=None,
        sast_config="auto",
        findings=None,
        output_dir=str(artifact_dir),
        agent_mode=request.agent_mode,
        max_iterations=request.max_iterations,
        full_reports=False,
        base_ref=None,
        head_ref="HEAD",
        base_findings=None,
        base_architecture=None,
        cancellation_token=cancellation_token,
    )


def _read_gate(artifact_dir: Path) -> GateResult:
    path = artifact_dir / "gate.json"
    if not path.is_file():
        raise FileNotFoundError("Pipeline did not produce gate.json")
    return GateResult.model_validate_json(path.read_text(encoding="utf-8"))


def _read_reports(artifact_dir: Path) -> list[tuple[FinalReport, Path]]:
    reports_dir = artifact_dir / "reports"
    if not reports_dir.is_dir():
        return []
    reports: list[tuple[FinalReport, Path]] = []
    for path in sorted(reports_dir.glob("*.json")):
        reports.append(
            (
                FinalReport.model_validate_json(path.read_text(encoding="utf-8")),
                path,
            )
        )
    return reports


def _default_pipeline_runner(args: argparse.Namespace) -> int:
    from app.ci_pipeline import run_ci_pipeline

    return run_ci_pipeline(args)
