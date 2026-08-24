from __future__ import annotations

import argparse
import io
import re
import shutil
import stat
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from uuid import uuid4

import yaml

from agent.llm import LLMUnavailableError, ProviderFailoverClient, parse_route_specs
from api.registry import ApiTargetRegistry, RegisteredTarget
from api.store import ApiStore
from app.config import settings
from discovery.service import ProjectDiscovery
from pipeline.errors import error_from_exception
from schemas.api import (
    ApiEvidence,
    ApiFinding,
    ApiProject,
    ApiRun,
    ChatLLMAnswer,
    ChatSession,
    ChatSnapshot,
    CreateChatSessionRequest,
    CreateRunRequest,
    FindingTimeline,
    RunTimeline,
    SendChatMessageRequest,
)
from schemas.pipeline import GateResult
from schemas.report import FinalReport
from schemas.target import TargetProfile

PipelineRunner = Callable[[argparse.Namespace], int]
ChatAnswerer = Callable[[str, dict[str, object]], str]

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
    """API orchestration, chat sessions and uploaded-project discovery."""

    def __init__(
        self,
        *,
        registry: ApiTargetRegistry,
        store: ApiStore,
        artifact_root: str | Path,
        project_root: str | Path | None = None,
        max_upload_bytes: int | None = None,
        pipeline_runner: PipelineRunner | None = None,
        executor: ThreadPoolExecutor | None = None,
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
        # T29 owns parallel scheduling. T23/T24 deliberately serialize real runs.
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="cft-api-run",
        )
        self._owns_executor = executor is None

        self._load_generated_projects()
        for target in self.registry.list():
            self.store.upsert_project(
                profile_path=target.profile_path,
                profile=target.profile,
            )

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
                # Only server-generated profiles are considered; a broken orphan is ignored.
                continue

    def close(self) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=False)

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
        architecture_path = workspace / "architecture.yaml"
        profile_path = workspace / "target-profile.yaml"
        workspace.mkdir(parents=True, exist_ok=False)

        try:
            _extract_project_archive(content, extract_root)
            repository = _repository_root(extract_root)
            discovery_api = ProjectDiscovery()
            discovery = discovery_api.discover(repository)
            if not discovery.components:
                raise ProjectImportError(
                    "Discovery found no runnable project components in the archive"
                )

            profile = discovery_api.build_profile(
                discovery,
                profile_id=target_id,
                name=_project_name(filename),
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
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise

    def create_run(
        self,
        request: CreateRunRequest,
        *,
        chat_session_id: str | None = None,
    ) -> ApiRun:
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
            analysis_request=request.analysis_request,
            artifact_dir=artifact_dir,
        )
        if chat_session_id is not None:
            self.store.set_chat_run(chat_session_id, run_id)
        self._executor.submit(
            self._execute_run,
            run_id,
            target,
            request,
            artifact_dir,
            chat_session_id,
        )
        return run

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

    def create_chat_session(self, request: CreateChatSessionRequest) -> ChatSession:
        self.registry.get(request.target_id)
        project = self.store.get_project(request.target_id)
        title = (request.title or project.name or project.id).strip()
        return self.store.create_chat_session(target_id=request.target_id, title=title)

    def list_chat_sessions(self, *, limit: int = 100) -> list[ChatSession]:
        return self.store.list_chat_sessions(limit=limit)

    def get_chat_snapshot(self, session_id: str) -> ChatSnapshot:
        session = self.store.get_chat_session(session_id)
        messages = self.store.list_chat_messages(session_id)
        if session.active_run_id is None:
            return ChatSnapshot(session=session, messages=messages)

        run = self.store.get_run(session.active_run_id)
        artifact_dir = self.store.artifact_dir(run.id)
        reports = _read_partial_reports(artifact_dir)
        gate = _read_optional_gate(artifact_dir)
        return ChatSnapshot(
            session=session,
            messages=messages,
            run=run,
            reports=reports,
            gate=gate,
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
        if current_run is not None and current_run.status in {"queued", "running"}:
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
                    "Принял. Запускаю Discovery → SAST → sandbox-анализ → Evidence → Gate. "
                    "Буду показывать реальные действия и Evidence по мере появления."
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
                f"По последнему запуску Gate: {decision}. "
                f"В отчёте {len(findings)} findings. "
                "Свободный ответ LLM сейчас недоступен, но фактические Evidence и отчёты "
                "остаются доступны в этом чате."
            )

    def _execute_run(
        self,
        run_id: str,
        target: RegisteredTarget,
        request: CreateRunRequest,
        artifact_dir: Path,
        chat_session_id: str | None,
    ) -> None:
        self.store.mark_running(run_id)
        try:
            args = _pipeline_args(
                target=target,
                request=request,
                artifact_dir=artifact_dir,
            )
            exit_code = self.pipeline_runner(args)
            gate = _read_gate(artifact_dir)
            reports = _read_reports(artifact_dir)
            self.store.replace_results(run_id, reports=reports)
            error = gate.errors[0] if exit_code == 2 and gate.errors else None
            if chat_session_id is not None:
                self.store.append_chat_message(
                    session_id=chat_session_id,
                    role="assistant",
                    kind="summary",
                    content=_run_summary(gate, [report for report, _ in reports]),
                    run_id=run_id,
                )
            # Terminal run status is persisted last so a completed snapshot already
            # contains the final assistant summary and all indexed results.
            self.store.mark_finished(
                run_id,
                exit_code=exit_code,
                gate_decision=gate.decision,
                error=error,
            )
        except Exception as exc:  # noqa: BLE001 - background boundary becomes persisted error
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

    def _require_completed(self, run_id: str) -> ApiRun:
        run = self.store.get_run(run_id)
        if run.status in {"queued", "running"}:
            raise RunNotReadyError("Run is still in progress")
        return run


def _pipeline_args(
    *,
    target: RegisteredTarget,
    request: CreateRunRequest,
    artifact_dir: Path,
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
    )


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
    # Discovery knows structure and stack, but must not invent criticality/trust relationships.
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
            # A background writer may still be finishing the file; the next SSE tick retries it.
            continue
    return reports


def _run_summary(gate: GateResult, reports: list[FinalReport]) -> str:
    counts: dict[str, int] = {}
    for report in reports:
        counts[report.status] = counts.get(report.status, 0) + 1
    parts = [f"Анализ завершён. Gate: {gate.decision.upper()}."]
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
    parts.append("Можешь задавать вопросы по findings, Evidence и решению Gate прямо здесь.")
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
            "Answer in the same language as the user. Use only the supplied completed-run data. "
            "Clearly distinguish deterministic Evidence from interpretation. Never invent a "
            "finding, action, file, verdict, or Gate decision. If the data does not answer the "
            "question, say that directly. Keep the answer useful and concise."
        ),
        user_payload={
            "question": question,
            "completed_run": context,
        },
        operation="chat_followup",
    )
    return result.answer.strip()


def _default_pipeline_runner(args: argparse.Namespace) -> int:
    # Keep the API importable for metadata/read-only clients; the heavy LangGraph
    # dependency is loaded only when a real analysis run starts.
    from app.ci_pipeline import run_ci_pipeline

    return run_ci_pipeline(args)
