from __future__ import annotations

import argparse
import base64
import binascii
import io
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from uuid import uuid4

import yaml

from agent.llm import LLMUnavailableError, ProviderFailoverClient, parse_route_specs
from api.registry import ApiTargetRegistry, RegisteredTarget
from api.scheduler import ResourceBudget, ResourceRequest, RunScheduler
from api.store import ApiStore
from app.config import settings
from discovery.service import ProjectDiscovery
from pipeline.cancellation import CancellationToken, RunCancelled, cancellation_scope
from pipeline.errors import error_from_exception
from pipeline.progress import PROGRESS_FILE_NAME, read_progress
from schemas.api import (
    ApiEvidence,
    ApiFinding,
    ApiProject,
    ApiRun,
    ChatLLMAnswer,
    ChatRunSnapshot,
    ChatSession,
    ChatSnapshot,
    CreateChatSessionRequest,
    CreateRunRequest,
    DiscoveryComponentView,
    FindingTimeline,
    ImportProjectFilesRequest,
    RunActivityEvent,
    RunDiscoveryView,
    RunFindingProgressEvent,
    RunProgress,
    RunStageEvent,
    RunTimeline,
    SendChatMessageRequest,
)
from schemas.pipeline import GateResult
from schemas.report import FinalReport
from schemas.target import TargetProfile

PipelineRunner = Callable[[argparse.Namespace], int]
ChatAnswerer = Callable[[str, dict[str, object]], str]

logger = logging.getLogger(__name__)

_REANALYZE = re.compile(
    r"(?:^|\s)(?:/analy[sz]e|/reanaly[sz]e|/scan)(?:\s|$)"
    r"|(?:перезапусти|повтори|повторно|запусти|проведи|проверь|исследуй|"
    r"проанализируй|поищи).{0,80}"
    r"|(?:rerun|reanalyze|re-analyze|analyze|analyse|scan|investigate|check).{0,80}",
    re.IGNORECASE,
)
_IGNORED_ARCHIVE_PARTS = {"__MACOSX", ".DS_Store"}
_MAX_ARCHIVE_ENTRIES = 10_000
_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_SINGLE_FILE_BYTES = 128 * 1024 * 1024


class RunNotReadyError(RuntimeError):
    pass


class ProjectImportError(ValueError):
    pass


class RunOrchestrator:
    """Ограниченная фоновая оркестрация поверх канонического CI pipeline"""

    def __init__(
        self,
        *,
        registry: ApiTargetRegistry,
        store: ApiStore,
        artifact_root: str | Path,
        project_root: str | Path | None = None,
        max_upload_bytes: int | None = None,
        pipeline_runner: PipelineRunner | None = None,
        scheduler: RunScheduler | None = None,
        resource_budget: ResourceBudget | None = None,
        resource_request: ResourceRequest | None = None,
        max_workers: int | None = None,
        chat_answerer: ChatAnswerer | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.artifact_root = Path(artifact_root).expanduser().resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.project_root = Path(
            project_root or settings.api_project_root
        ).expanduser().resolve()
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = max_upload_bytes or settings.api_max_upload_bytes
        self.pipeline_runner = pipeline_runner or _default_pipeline_runner
        self.chat_answerer = chat_answerer or _default_chat_answerer
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

        self._load_generated_projects()
        for target in self.registry.list():
            self.store.upsert_project(
                profile_path=target.profile_path,
                profile=target.profile,
            )
        self.store.recover_abandoned()
        self._restore_queued_runs()

    def _load_generated_projects(self) -> None:
        for profile_path in sorted(self.project_root.glob("upload-*/target-profile.yaml")):
            try:
                profile = TargetProfile.from_yaml(profile_path)
                if profile.repository_path is None or not profile.repository_path.is_dir():
                    continue
                try:
                    self.registry.get(profile.id)
                except KeyError:
                    self.registry.register_generated(
                        profile_path=profile_path,
                        profile=profile,
                    )
            except (OSError, TypeError, ValueError):
                continue

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

    def import_project_zip(self, *, filename: str, content: bytes) -> ApiProject:
        if not filename.lower().endswith(".zip"):
            raise ProjectImportError("Project upload must be a .zip archive")
        if not content:
            raise ProjectImportError("Project archive is empty")
        if len(content) > self.max_upload_bytes:
            raise ProjectImportError(
                f"Project archive exceeds {self.max_upload_bytes // (1024 * 1024)} MiB limit"
            )

        target_id = f"upload-{uuid4().hex[:16]}"
        workspace = self.project_root / target_id
        extract_root = workspace / "source"

        try:
            _extract_project_archive(content, extract_root)
            return self._register_discovered_project(
                workspace=workspace,
                extract_root=extract_root,
                target_id=target_id,
                display_name=_project_name(filename),
            )
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise

    def import_project_files(self, request: ImportProjectFilesRequest) -> ApiProject:
        """Импортируем выбранную в браузере папку как relative path и base64 файлы

        Импорт папки и ZIP сходится в общий staging + Discovery flow
        Project id и workspace принадлежат серверу, путям клиента не доверяем
        """
        display_name = (request.name or "Uploaded project").strip()[:120] or "Uploaded project"
        total_bytes = 0
        decoded: list[tuple[PurePosixPath, bytes]] = []
        seen: set[str] = set()
        for item in request.files:
            relative = _validated_manifest_path(item.path)
            if str(relative) in seen:
                raise ProjectImportError(f"Duplicate project file path: {item.path}")
            seen.add(str(relative))
            try:
                content = base64.b64decode(
                    "".join(item.content_base64.split()),
                    validate=True,
                )
            except (binascii.Error, ValueError) as exc:
                raise ProjectImportError(
                    f"Project file is not valid base64: {item.path}"
                ) from exc
            if len(content) > _MAX_SINGLE_FILE_BYTES:
                raise ProjectImportError(f"Project file is oversized: {item.path}")
            total_bytes += len(content)
            if total_bytes > self.max_upload_bytes:
                raise ProjectImportError(
                    "Project folder exceeds "
                    f"{self.max_upload_bytes // (1024 * 1024)} MiB limit"
                )
            decoded.append((relative, content))

        target_id = f"upload-{uuid4().hex[:16]}"
        workspace = self.project_root / target_id
        extract_root = workspace / "source"

        try:
            extract_root.mkdir(parents=True, exist_ok=False)
            root = extract_root.resolve()
            for relative, content in decoded:
                destination = (root / Path(*relative.parts)).resolve()
                try:
                    destination.relative_to(root)
                except ValueError as exc:
                    raise ProjectImportError(
                        "Project file escapes the project directory"
                    ) from exc
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)

            return self._register_discovered_project(
                workspace=workspace,
                extract_root=extract_root,
                target_id=target_id,
                display_name=display_name,
            )
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise

    def _register_discovered_project(
        self,
        *,
        workspace: Path,
        extract_root: Path,
        target_id: str,
        display_name: str,
    ) -> ApiProject:
        architecture_path = workspace / "architecture.yaml"
        profile_path = workspace / "target-profile.yaml"
        repository = _repository_root(extract_root)
        # Загруженному дереву нужен свой .git, иначе Semgrep унаследует ignore для api_data
        _init_staging_repository(repository)
        discovery_api = ProjectDiscovery()
        discovery = discovery_api.discover(repository)
        if not discovery.components:
            raise ProjectImportError(
                "Discovery found no runnable project components in the uploaded project"
            )

        profile = discovery_api.build_profile(
            discovery,
            profile_id=target_id,
            name=display_name,
            architecture_file=architecture_path,
        )
        _write_discovered_architecture(profile, architecture_path)
        profile_path.write_text(
            yaml.safe_dump(
                profile.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self.registry.register_generated(profile_path=profile_path, profile=profile)
        self.store.upsert_project(profile_path=profile_path, profile=profile)
        return self.store.get_project(target_id)

    def create_run(
        self,
        request: CreateRunRequest,
        *,
        chat_session_id: str | None = None,
    ) -> ApiRun:
        if self._closed:
            raise RuntimeError("Run orchestrator is closed")
        target = self.registry.get(request.target_id)
        repository = target.profile.repository_path
        if repository is None:
            raise ValueError("Registered TargetProfile has no repository_path")
        if not repository.is_dir():
            raise ValueError("Registered target repository is not available")

        if chat_session_id is not None:
            session = self.store.get_chat_session(chat_session_id)
            if session.target_id != request.target_id:
                raise ValueError("Chat session target does not match run target")

        run_id = f"run-{uuid4().hex}"
        artifact_dir = self.artifact_root / run_id
        run = self.store.create_run(
            run_id=run_id,
            target_id=request.target_id,
            agent_mode=request.agent_mode,
            max_iterations=request.max_iterations,
            artifact_dir=artifact_dir,
            analysis_request=request.analysis_request,
        )
        if chat_session_id is not None:
            self.store.set_chat_run(chat_session_id, run_id)
        self._schedule(
            run_id,
            target,
            request,
            artifact_dir,
            chat_session_id=chat_session_id,
        )
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
        return _public_gate(
            GateResult.model_validate_json(path.read_text(encoding="utf-8"))
        )

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

    def get_run_progress(self, run_id: str) -> RunProgress:
        """Прогресс запуска можно безопасно опрашивать в очереди и во время работы"""
        artifact_dir = self.store.artifact_dir(run_id)
        return _build_run_progress(artifact_dir)

    def get_run_discovery(self, run_id: str) -> RunDiscoveryView:
        artifact_dir = self.store.artifact_dir(run_id)
        view = _read_discovery_view(artifact_dir)
        if view is None:
            raise FileNotFoundError("Run discovery artifact is not available yet")
        return view

    def create_chat_session(self, request: CreateChatSessionRequest) -> ChatSession:
        self.registry.get(request.target_id)
        project = self.store.get_project(request.target_id)
        if not project.repository_available:
            # Чат навсегда привязан к проекту, без локального checkout анализ не запустить
            raise ProjectImportError(
                "Project repository is not available on the server"
            )
        title = (request.title or project.name or project.id).strip()
        return self.store.create_chat_session(target_id=request.target_id, title=title)

    def list_chat_sessions(self, *, limit: int = 100) -> list[ChatSession]:
        return self.store.list_chat_sessions(limit=limit)

    def delete_chat_session(self, session_id: str) -> None:
        self.store.delete_chat_session(session_id)

    def get_chat_snapshot(self, session_id: str) -> ChatSnapshot:
        session = self.store.get_chat_session(session_id)
        messages = self.store.list_chat_messages(session_id)
        run_snapshots = [
            self._chat_run_snapshot(run_id)
            for run_id in self.store.list_chat_run_ids(session_id)
        ]
        if session.active_run_id is None:
            return ChatSnapshot(
                session=session,
                messages=messages,
                runs=run_snapshots,
            )

        active = next(
            (
                item
                for item in reversed(run_snapshots)
                if item.run.id == session.active_run_id
            ),
            None,
        )
        if active is None:
            active = self._chat_run_snapshot(session.active_run_id)
            run_snapshots.append(active)
        return ChatSnapshot(
            session=session,
            messages=messages,
            run=active.run,
            reports=active.reports,
            gate=active.gate,
            progress=active.progress,
            discovery=active.discovery,
            runs=run_snapshots,
        )

    def _chat_run_snapshot(self, run_id: str) -> ChatRunSnapshot:
        run = self.store.get_run(run_id)
        artifact_dir = self.store.artifact_dir(run.id)
        return ChatRunSnapshot(
            run=run,
            reports=_read_partial_reports(artifact_dir),
            gate=_public_gate(_read_optional_gate(artifact_dir)),
            progress=_build_run_progress(artifact_dir),
            discovery=_read_discovery_view(artifact_dir),
        )

    def send_chat_message(
        self,
        session_id: str,
        request: SendChatMessageRequest,
    ) -> ChatSnapshot:
        session = self.store.get_chat_session(session_id)
        content = request.content.strip()
        self.store.append_chat_message(
            session_id=session_id,
            role="user",
            content=content,
        )

        current_run = (
            self.store.get_run(session.active_run_id)
            if session.active_run_id is not None
            else None
        )
        if current_run is not None and current_run.status in {"queued", "running", "cancelling"}:
            self.store.append_chat_message(
                session_id=session_id,
                role="assistant",
                kind="status",
                content=(
                    "Анализ уже выполняется. Я не запускаю второй sandbox параллельно; "
                    "текущий прогресс появится здесь автоматически."
                ),
                run_id=current_run.id,
            )
            return self.get_chat_snapshot(session_id)

        should_run = current_run is None or _REANALYZE.search(content) is not None
        if should_run:
            self.store.append_chat_message(
                session_id=session_id,
                role="assistant",
                kind="status",
                content=(
                    "Принял. Запускаю исследование проекта → статический анализ → "
                    "проверку в песочнице → сбор доказательств → итоговое решение. "
                    "Буду показывать реальные действия и доказательства по мере появления."
                ),
            )
            self.create_run(
                CreateRunRequest(
                    target_id=session.target_id,
                    agent_mode=request.agent_mode,
                    max_iterations=request.max_iterations,
                    analysis_request=content,
                ),
                chat_session_id=session_id,
            )
            return self.get_chat_snapshot(session_id)

        answer = self._answer_followup(current_run, content)
        self.store.append_chat_message(
            session_id=session_id,
            role="assistant",
            content=answer,
            run_id=current_run.id,
        )
        return self.get_chat_snapshot(session_id)

    def _answer_followup(self, run: ApiRun, question: str) -> str:
        if run.status == "technical_failure":
            message = run.error.message if run.error is not None else "unknown pipeline failure"
            return f"Предыдущий анализ завершился технической ошибкой: {message}"

        context = _chat_context(run, self.store.artifact_dir(run.id))
        try:
            return self.chat_answerer(question, context)
        except (LLMUnavailableError, RuntimeError, ValueError, TypeError):
            gate = context.get("gate") or {}
            findings = context.get("findings") or []
            decision = gate.get("decision", "unknown") if isinstance(gate, dict) else "unknown"
            return (
                f"Решение последнего запуска: {decision}. "
                f"В отчете находок: {len(findings)}. "
                "Свободный ответ языковой модели сейчас недоступен, но доказательства и отчеты "
                "остаются доступны в этом чате."
            )

    def _render_run_summary(
        self,
        request: CreateRunRequest,
        gate: GateResult,
        reports: list[FinalReport],
        *,
        technical_failure: bool,
    ) -> str:
        fallback = _run_summary(
            gate,
            reports,
            technical_failure=technical_failure,
        )
        if request.agent_mode != "llm":
            return fallback
        try:
            answer = self.chat_answerer(
                (
                    "Кратко и естественно объясни пользователю итог security-анализа "
                    "на русском языке. Опирайся только на переданные Gate, findings и "
                    "Evidence. Если это техническая ошибка, прямо скажи, что security-"
                    "вердикт не сформирован, и назови доступную причину. Используй русские "
                    "термины везде, где это не имя технологии, файла, правила или идентификатор: "
                    "пиши «решение», «находка», «доказательство», «подтверждено», "
                    "«недостаточно данных» вместо Gate, finding, Evidence, confirmed и "
                    "inconclusive. Возвращай корректный Markdown без HTML."
                ),
                _summary_context(gate, reports),
            ).strip()
            return answer or fallback
        except (LLMUnavailableError, RuntimeError, ValueError, TypeError):
            return fallback

    def _execute_run(
        self,
        run_id: str,
        target: RegisteredTarget,
        request: CreateRunRequest,
        artifact_dir: Path,
        token: CancellationToken,
        chat_session_id: str | None = None,
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
                if chat_session_id is not None:
                    self.store.append_chat_message(
                        session_id=chat_session_id,
                        role="assistant",
                        kind="summary",
                        content=self._render_run_summary(
                            request,
                            gate,
                            [report for report, _ in reports],
                            technical_failure=exit_code == 2,
                        ),
                        run_id=run_id,
                    )
                if not self.store.mark_finished(
                    run_id,
                    exit_code=exit_code,
                    gate_decision=gate.decision,
                    error=error,
                ):
                    cancelled = self.store.get_run(run_id).status == "cancelling"
        except RunCancelled:
            cancelled = True
        except Exception as exc:
            logger.exception("Run %s failed with an orchestration error", run_id)
            error = error_from_exception(
                exc,
                layer="pipeline",
                public_message="API orchestration failed",
            )
            if chat_session_id is not None:
                self.store.append_chat_message(
                    session_id=chat_session_id,
                    role="assistant",
                    kind="error",
                    content=f"Анализ остановлен из-за технической ошибки: {error.message}",
                    run_id=run_id,
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
        *,
        chat_session_id: str | None = None,
    ) -> None:
        self._scheduler.submit(
            run_id,
            lambda token: self._execute_run(
                run_id,
                target,
                request,
                artifact_dir,
                token,
                chat_session_id,
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
                        analysis_request=job.analysis_request,
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
        analysis_request=request.analysis_request,
        full_reports=False,
        base_ref=None,
        head_ref="HEAD",
        base_findings=None,
        base_architecture=None,
        cancellation_token=cancellation_token,
    )


def _validated_manifest_path(raw_path: str) -> PurePosixPath:
    if raw_path.startswith(("/", "\\")):
        raise ProjectImportError("Project file path must be relative")
    normalized = raw_path.replace("\\", "/").strip().strip("/")
    if not normalized:
        raise ProjectImportError("Project file path is empty")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or ".." in relative.parts:
        raise ProjectImportError("Project file path escapes the project directory")
    if any(part in {"", "."} for part in relative.parts):
        raise ProjectImportError(f"Project file path is not normalized: {raw_path}")
    if ":" in relative.parts[0]:
        raise ProjectImportError("Project file contains an absolute Windows path")
    if len(relative.parts) > 32:
        raise ProjectImportError("Project file path is too deep")
    return relative


def _build_run_progress(artifact_dir: Path) -> RunProgress:
    raw_events = read_progress(artifact_dir / PROGRESS_FILE_NAME)
    stages = [
        RunStageEvent(
            stage=str(event.get("stage", "pipeline"))[:32],
            status=str(event.get("status", "running")),
            detail=(
                str(event["detail"])[:500]
                if event.get("detail") is not None
                else None
            ),
            at=str(event["ts"]) if event.get("ts") is not None else None,
        )
        for event in raw_events
        if event.get("kind") == "stage"
    ]
    activities = _read_audit_activities(artifact_dir)

    findings_total: int | None = None
    current_finding: str | None = None
    finished: set[str] = set()
    finding_events: list[RunFindingProgressEvent] = []
    for event in raw_events:
        kind = event.get("kind")
        if kind == "finding_started":
            total = event.get("total")
            if isinstance(total, int) and total > 0:
                findings_total = total
            finding_id = str(event.get("finding_id", ""))
            if finding_id and finding_id not in finished:
                title = event.get("title")
                current_finding = (
                    str(title)[:300] if title else str(event.get("rule_id", ""))
                )
                finding_events.append(
                    RunFindingProgressEvent(
                        finding_id=finding_id[:256],
                        status="started",
                        title=str(title)[:300] if title else None,
                        severity=(
                            str(event["severity"])[:32]
                            if event.get("severity") is not None
                            else None
                        ),
                        rule_id=(
                            str(event["rule_id"])[:300]
                            if event.get("rule_id") is not None
                            else None
                        ),
                        file=(
                            str(event["file"])[:1024]
                            if event.get("file") is not None
                            else None
                        ),
                        index=(
                            event["index"]
                            if isinstance(event.get("index"), int)
                            and event["index"] > 0
                            else None
                        ),
                        total=(
                            total if isinstance(total, int) and total > 0 else None
                        ),
                        at=str(event["ts"]) if event.get("ts") is not None else None,
                    )
                )
        elif kind == "finding_finished":
            finding_id = str(event.get("finding_id", ""))
            if finding_id:
                finished.add(finding_id)
                if current_finding is not None:
                    current_finding = None
                finding_events.append(
                    RunFindingProgressEvent(
                        finding_id=finding_id[:256],
                        status="finished",
                        result=(
                            str(event["status"])[:32]
                            if event.get("status") is not None
                            else None
                        ),
                        at=str(event["ts"]) if event.get("ts") is not None else None,
                    )
                )

    return RunProgress(
        stages=stages,
        activities=activities,
        finding_events=finding_events,
        findings_total=findings_total,
        findings_done=len(finished),
        current_finding=current_finding,
    )


def _read_audit_activities(artifact_dir: Path) -> list[RunActivityEvent]:
    audit_path = artifact_dir / "audit" / "executor.jsonl"
    events: list[RunActivityEvent] = []
    try:
        raw_lines = audit_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in raw_lines[-100:]:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        action_id = record.get("action_id")
        tool = record.get("tool")
        if not action_id or not tool:
            continue
        exit_code = record.get("exit_code")
        duration_ms = record.get("duration_ms")
        events.append(
            RunActivityEvent(
                action_id=str(action_id)[:128],
                tool=str(tool)[:128],
                target=(
                    str(record["target"])[:200]
                    if record.get("target") is not None
                    else None
                ),
                status=str(record["status"])[:64] if record.get("status") else None,
                exit_code=exit_code if isinstance(exit_code, int) else None,
                duration_ms=duration_ms if isinstance(duration_ms, int) else None,
                at=str(record["timestamp"]) if record.get("timestamp") else None,
            )
        )
    return events


def _read_discovery_view(artifact_dir: Path) -> RunDiscoveryView | None:
    discovery_payload = _read_optional_json(artifact_dir / "discovery.json")
    profile_payload = _read_optional_json(artifact_dir / "target-profile.json")
    if discovery_payload is None:
        return None

    components: list[DiscoveryComponentView] = []
    technologies: list[str] = []
    for raw in discovery_payload.get("components") or []:
        if not isinstance(raw, dict):
            continue
        component_tech = [str(item) for item in raw.get("technologies") or []]
        frameworks = [str(item) for item in raw.get("frameworks") or []]
        for name in [*component_tech, *frameworks]:
            if name and name not in technologies:
                technologies.append(name)
        components.append(
            DiscoveryComponentView(
                id=str(raw.get("id", ""))[:120],
                root=str(raw.get("root", "."))[:200],
                technologies=component_tech[:12],
                frameworks=frameworks[:12],
                dependency_files=[str(item) for item in (raw.get("dependency_files") or [])][:16],
                dockerfiles=[str(item) for item in (raw.get("dockerfiles") or [])][:16],
                local_addresses=[
                    str(item) for item in (raw.get("allowed_local_addresses") or [])
                ][:8],
            )
        )

    services: list[str] = []
    if isinstance(profile_payload, dict):
        raw_services = profile_payload.get("services")
        if isinstance(raw_services, dict):
            services = sorted(str(key) for key in raw_services)

    return RunDiscoveryView(
        components=components[:24],
        services=services[:24],
        technologies=technologies[:24],
        warnings=[str(item)[:300] for item in (discovery_payload.get("warnings") or [])][:10],
    )


def _read_optional_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _extract_project_archive(content: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ProjectImportError("Uploaded file is not a valid ZIP archive") from exc

    with archive:
        entries = archive.infolist()
        if len(entries) > _MAX_ARCHIVE_ENTRIES:
            raise ProjectImportError("ZIP contains too many entries")
        total_uncompressed = sum(item.file_size for item in entries if not item.is_dir())
        if total_uncompressed > _MAX_UNCOMPRESSED_BYTES:
            raise ProjectImportError("ZIP expands beyond the allowed project size")

        root = destination.resolve()
        for info in entries:
            normalized = info.filename.replace("\\", "/").strip("/")
            if not normalized:
                continue
            relative = PurePosixPath(normalized)
            if relative.is_absolute() or ".." in relative.parts:
                raise ProjectImportError("ZIP entry escapes the project directory")
            if relative.parts[0].endswith(":"):
                raise ProjectImportError("ZIP contains an absolute Windows path")
            if any(part in _IGNORED_ARCHIVE_PARTS for part in relative.parts):
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise ProjectImportError("ZIP symbolic links are not allowed")
            if info.file_size > _MAX_SINGLE_FILE_BYTES:
                raise ProjectImportError("ZIP contains an oversized file")

            target = (root / Path(*relative.parts)).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ProjectImportError("ZIP entry escapes the project directory") from exc

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _init_staging_repository(root: Path) -> None:
    """Best-effort `git init` и index для staged upload, импорт из-за этого не падает

    Staging должен выглядеть как обычный checkout для Semgrep и diff tools
    Трогаем только локальный index, без commit, identity и network
    """
    try:
        subprocess.run(
            ["git", "init", "-q"],
            cwd=root,
            capture_output=True,
            timeout=30,
            check=False,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=root,
            capture_output=True,
            timeout=120,
            check=False,
            env={**os.environ, "GIT_AUTHOR_NAME": "cft", "GIT_AUTHOR_EMAIL": "cft@local",
                 "GIT_COMMITTER_NAME": "cft", "GIT_COMMITTER_EMAIL": "cft@local"},
        )
    except (OSError, subprocess.SubprocessError):
        return


def _repository_root(extract_root: Path) -> Path:
    children = [item for item in extract_root.iterdir() if item.name not in _IGNORED_ARCHIVE_PARTS]
    if len(children) == 1 and children[0].is_dir():
        return children[0].resolve()
    return extract_root.resolve()


def _project_name(filename: str) -> str:
    name = Path(filename).name
    if name.lower().endswith(".zip"):
        name = name[:-4]
    return name.strip()[:120] or "Uploaded project"


def _write_discovered_architecture(profile, path: Path) -> None:
    # Discovery знает структуру и stack, но не выдумывает criticality и trust relationships
    payload = {
        "services": {
            service_id: {
                "type": service.type,
                "public": False,
                "criticality": "unknown",
                "connects_to": [],
                "authentication": "unknown",
                "blast_radius": "unknown",
            }
            for service_id, service in sorted(profile.services.items())
        }
    }
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _read_gate(artifact_dir: Path) -> GateResult:
    path = artifact_dir / "gate.json"
    if not path.is_file():
        raise FileNotFoundError("Pipeline did not produce gate.json")
    return GateResult.model_validate_json(path.read_text(encoding="utf-8"))


def _read_optional_gate(artifact_dir: Path) -> GateResult | None:
    path = artifact_dir / "gate.json"
    if not path.is_file():
        return None
    try:
        return GateResult.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _public_gate(gate: GateResult | None) -> GateResult | None:
    """Убираем серверные пути артефактов из представлений API и чата"""
    if gate is None:
        return None
    return gate.model_copy(
        update={
            "findings": [
                finding.model_copy(update={"report_path": None})
                for finding in gate.findings
            ]
        }
    )

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


def _read_partial_reports(artifact_dir: Path) -> list[FinalReport]:
    reports_dir = artifact_dir / "reports"
    if not reports_dir.is_dir():
        return []
    reports: list[FinalReport] = []
    for path in sorted(reports_dir.glob("*.json")):
        try:
            reports.append(FinalReport.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return reports


def _run_summary(
    gate: GateResult,
    reports: list[FinalReport],
    *,
    technical_failure: bool = False,
) -> str:
    if technical_failure:
        error = gate.errors[0] if gate.errors else None
        detail = error.message if error is not None else "неизвестная ошибка pipeline"
        return (
            f"Анализ остановлен из-за технической ошибки: {detail}. "
            "Результат анализа безопасности не сформирован; находки и доказательства не оценивались. "
            "Можно повторить запуск после устранения причины."
        )
    counts: dict[str, int] = {}
    for report in reports:
        counts[report.status] = counts.get(report.status, 0) + 1
    decision_labels = {"pass": "пройден", "warn": "с предупреждениями", "fail": "не пройден"}
    parts = [f"Анализ завершен. Решение: {decision_labels.get(gate.decision, gate.decision)}."]
    if reports:
        parts.append(
            "Findings: "
            + ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
            + "."
        )
    important = [
        report
        for report in reports
        if report.status in {"confirmed", "policy_blocked"}
    ][:3]
    if important:
        parts.append(
            "Главное: "
            + "; ".join(
                f"{report.finding.title} ({report.finding.severity or 'severity N/A'})"
                for report in important
            )
            + "."
        )
    parts.append("Можешь задавать вопросы по находкам, доказательствам и итоговому решению прямо здесь.")
    return " ".join(parts)


def _chat_context(run: ApiRun, artifact_dir: Path) -> dict[str, object]:
    gate = _read_optional_gate(artifact_dir)
    reports = _read_partial_reports(artifact_dir)
    findings: list[dict[str, object]] = []
    for report in reports[:30]:
        findings.append(
            {
                "finding_id": report.finding_id,
                "title": report.finding.title,
                "rule_id": report.finding.rule_id,
                "severity": report.finding.severity,
                "status": report.status,
                "file": report.finding.file,
                "line": report.finding.line_start,
                "analysis_summary": report.analysis_summary,
                "hypothesis": report.hypothesis,
                "explanation": report.explanation,
                "next_step": report.next_step,
                "agent_decisions": [
                    {
                        "step": item.step,
                        "outcome": item.outcome,
                        "reason": item.reason,
                        "stop_reason": item.stop_reason,
                    }
                    for item in report.agent_decisions[:8]
                ],
                "sandbox_actions": [
                    {
                        "capability": item.capability,
                        "purpose": item.purpose,
                        "execution_status": item.execution_status,
                        "exit_code": item.exit_code,
                    }
                    for item in report.sandbox_actions[:8]
                ],
                "evidence": [
                    {
                        "type": item.type,
                        "summary": item.summary,
                        "verdict": item.verdict,
                        "reliability": item.reliability,
                    }
                    for item in report.evidence[:8]
                ],
            }
        )
    return {
        "run": run.model_dump(mode="json"),
        "gate": gate.model_dump(mode="json") if gate is not None else None,
        "findings": findings,
    }


def _summary_context(
    gate: GateResult,
    reports: list[FinalReport],
) -> dict[str, object]:
    public_gate = _public_gate(gate)
    return {
        "gate": public_gate.model_dump(mode="json") if public_gate is not None else None,
        "findings": [
            {
                "finding_id": report.finding_id,
                "title": report.finding.title,
                "severity": report.finding.severity,
                "status": report.status,
                "analysis_summary": report.analysis_summary,
                "explanation": report.explanation,
                "next_step": report.next_step,
                "evidence": [
                    {
                        "type": item.type,
                        "summary": item.summary,
                        "verdict": item.verdict,
                        "reliability": item.reliability,
                    }
                    for item in report.evidence[:8]
                ],
            }
            for report in reports[:30]
        ],
    }


def _default_chat_answerer(question: str, context: dict[str, object]) -> str:
    client = ProviderFailoverClient(
        routes=parse_route_specs(settings.llm_routes),
        credentials=settings.llm_provider_credentials(),
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=min(1800, max(400, settings.llm_max_output_tokens)),
        trace=settings.llm_trace,
    )
    result = client.complete_model(
        output_model=ChatLLMAnswer,
        system_prompt=(
            "You are the conversational interface of a defensive security analysis system. "
            "Answer in the same language as the user. Use only the supplied trusted event or "
            "run data. "
            "Clearly distinguish deterministic Evidence from interpretation. Never invent a "
            "finding, action, file, verdict, or Gate decision. If the data does not answer the "
            "question, say that directly. Keep the answer useful and concise."
        ),
        user_payload={"question": question, "completed_run": context},
        operation="chat_followup",
    )
    return result.answer.strip()


def _default_pipeline_runner(args: argparse.Namespace) -> int:
    # Тяжелый LangGraph грузим только при старте анализа, чтобы read-only API оставался легким
    from app.config import settings as pipeline_settings

    if pipeline_settings.sandbox_image:
        # Readiness probe читает значение из process environment, а не из settings object
        os.environ.setdefault("CFT_SANDBOX_IMAGE", pipeline_settings.sandbox_image)

    from app.ci_pipeline import run_ci_pipeline

    return run_ci_pipeline(args)
