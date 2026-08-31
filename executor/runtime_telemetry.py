"""Сбор telemetry только из контейнеров текущей target-сессии."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from executor.sandbox import _communicate_bounded
from executor.sandbox_manager import SandboxLog
from pipeline.cancellation import RunCancelled, check_cancelled
from schemas.runtime_telemetry import RuntimeTelemetryEvent, RuntimeTelemetryTimeline
from schemas.target import TargetProfile
from security.error_redaction import redact_error_message

DockerRunner = Callable[[list[str], Path, float], subprocess.CompletedProcess[str]]
_DOCKER_OUTPUT_LIMIT = 1_048_576


class TelemetrySession(Protocol):
    session_id: str
    target: TargetProfile
    logs: list[SandboxLog]

    def collect_state(self) -> dict[str, object]: ...


def _run_docker(
    argv: list[str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    stdout, stdout_truncated, stderr, stderr_truncated, timed_out = (
        _communicate_bounded(process, b"", timeout, _DOCKER_OUTPUT_LIMIT)
    )
    return subprocess.CompletedProcess(
        argv,
        124 if timed_out else (process.returncode or 0),
        _decoded_output(stdout, stdout_truncated),
        _decoded_output(stderr, stderr_truncated),
    )


class RuntimeTelemetryCollector:
    """Строит timeline, не обследуя процессы, порты или файлы хоста."""

    def __init__(
        self,
        session: TelemetrySession,
        *,
        runner: DockerRunner = _run_docker,
        command_timeout: float = 10.0,
        max_events: int = 500,
        max_text_bytes: int = 4_096,
    ) -> None:
        if command_timeout <= 0 or max_events < 1 or max_text_bytes < 64:
            raise ValueError("Telemetry limits must be positive")
        self.session = session
        self.runner = runner
        self.command_timeout = command_timeout
        self.max_events = max_events
        self.max_text_bytes = max_text_bytes

        target = session.target
        self.target_id = str(target.id)
        repository_path = target.repository_path
        self.cwd = Path(repository_path) if repository_path else Path.cwd()

    def collect(self, *, run_id: str | None = None) -> RuntimeTelemetryTimeline:
        events: list[RuntimeTelemetryEvent] = []
        try:
            state = self.session.collect_state()
        except RunCancelled:
            raise
        except Exception:  # noqa: BLE001 - пробел в telemetry превращаем в structured event
            state = {}
            events.append(self._error("collect_session_state", run_id))

        events.extend(self._manager_events(run_id))
        label_name, label_value = self._session_label(state)
        containers = self._discover_containers(label_name, label_value, run_id, events)
        # Срез событий делается до служебных docker exec,
        # чтобы probes самого сборщика не попали в timeline.
        self._collect_docker_events(label_name, label_value, run_id, events)

        for container_id in containers:
            inspected = self._inspect_container(
                container_id, label_name, label_value, run_id, events
            )
            if inspected is None:
                continue
            service, running = inspected
            self._collect_logs(container_id, service, run_id, events)
            if running:
                self._collect_ports(container_id, service, run_id, events)
        events = self._ordered(events, run_id)
        return RuntimeTelemetryTimeline(
            session_id=self.session.session_id,
            run_id=run_id,
            target=self.target_id,
            events=events,
        )

    def _session_label(self, state: dict[str, object]) -> tuple[str, str]:
        project = state.get("compose_project")
        if isinstance(project, str) and project:
            return "com.docker.compose.project", project
        return "cft.session_id", self.session.session_id

    def _call(self, argv: list[str]) -> subprocess.CompletedProcess[str] | None:
        check_cancelled()
        try:
            return self.runner(argv, self.cwd, self.command_timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _discover_containers(
        self,
        label_name: str,
        label_value: str,
        run_id: str | None,
        events: list[RuntimeTelemetryEvent],
    ) -> list[str]:
        result = self._call(
            [
                "docker",
                "ps",
                "--all",
                "--filter",
                f"label={label_name}={label_value}",
                "--format",
                "{{.ID}}",
            ]
        )
        if result is None or result.returncode:
            events.append(self._error("discover_session_containers", run_id))
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _inspect_container(
        self,
        container_id: str,
        label_name: str,
        label_value: str,
        run_id: str | None,
        events: list[RuntimeTelemetryEvent],
    ) -> tuple[str | None, bool] | None:
        result = self._call(["docker", "inspect", "--type", "container", container_id])
        if result is None or result.returncode:
            events.append(self._error("inspect_session_container", run_id))
            return None
        try:
            records = json.loads(result.stdout)
            record = records[0]
            labels = record.get("Config", {}).get("Labels", {}) or {}
            state = record.get("State", {}) or {}
        except (IndexError, TypeError, AttributeError, json.JSONDecodeError):
            events.append(self._error("parse_container_inspect", run_id))
            return None

        # Docker-фильтр проверяется повторно:
        # чужой контейнер не должен попасть в timeline.
        if labels.get(label_name) != label_value:
            events.append(self._error("reject_unscoped_container", run_id))
            return None

        service = labels.get("com.docker.compose.service") or labels.get("cft.service")
        health = state.get("Health", {}) or {}
        facts: dict[str, object] = {
            "status": str(state.get("Status", "unknown")),
            "running": bool(state.get("Running", False)),
            "exit_code": _integer(state.get("ExitCode")),
            "health": str(health.get("Status", "unknown")),
            "started_at": str(state.get("StartedAt", "")),
            "finished_at": str(state.get("FinishedAt", "")),
        }
        events.append(
            self._event(
                run_id=run_id,
                kind="container_state",
                source="docker_inspect",
                level="error" if facts["status"] in {"dead", "exited"} else "info",
                service=service,
                container_id=container_id,
                facts=facts,
            )
        )
        return service, bool(state.get("Running", False))

    def _collect_logs(
        self,
        container_id: str,
        service: str | None,
        run_id: str | None,
        events: list[RuntimeTelemetryEvent],
    ) -> None:
        result = self._call(["docker", "logs", "--timestamps", container_id])
        if result is None or result.returncode:
            events.append(self._error("read_container_logs", run_id, service))
            return
        for stream, content in (("stdout", result.stdout), ("stderr", result.stderr)):
            for line in content.splitlines():
                observed_at, message = _split_log_line(line)
                if not message:
                    continue
                events.append(
                    self._event(
                        run_id=run_id,
                        kind="runtime_error" if stream == "stderr" else "runtime_log",
                        source="docker_logs",
                        level="error" if stream == "stderr" else "info",
                        service=service,
                        container_id=container_id,
                        observed_at=observed_at,
                        facts={
                            "stream": stream,
                            "message": self._safe_text(message),
                        },
                    )
                )

    def _collect_ports(
        self,
        container_id: str,
        service: str | None,
        run_id: str | None,
        events: list[RuntimeTelemetryEvent],
    ) -> None:
        seen: set[tuple[str, int]] = set()
        for family, proc_file in (("ipv4", "/proc/net/tcp"), ("ipv6", "/proc/net/tcp6")):
            result = self._call(["docker", "exec", container_id, "cat", proc_file])
            if result is None or result.returncode:
                continue
            for port in _listening_ports(result.stdout):
                key = (family, port)
                if key in seen:
                    continue
                seen.add(key)
                events.append(
                    self._event(
                        run_id=run_id,
                        kind="listening_port",
                        source="container_procfs",
                        service=service,
                        container_id=container_id,
                        facts={"family": family, "port": port, "protocol": "tcp"},
                    )
                )

    def _collect_docker_events(
        self,
        label_name: str,
        label_value: str,
        run_id: str | None,
        events: list[RuntimeTelemetryEvent],
    ) -> None:
        started_at = min(
            (log.captured_at for log in self.session.logs),
            default=datetime.now(UTC),
        )
        finished_at = datetime.now(UTC)
        result = self._call(
            [
                "docker",
                "events",
                "--since",
                started_at.isoformat(),
                "--until",
                finished_at.isoformat(),
                "--filter",
                f"label={label_name}={label_value}",
                "--format",
                "{{json .}}",
            ]
        )
        if result is None or result.returncode:
            events.append(self._error("read_session_docker_events", run_id))
            return
        for line in result.stdout.splitlines():
            try:
                record = json.loads(line)
                attributes = record.get("Actor", {}).get("Attributes", {}) or {}
            except (TypeError, AttributeError, json.JSONDecodeError):
                events.append(self._error("parse_docker_event", run_id))
                continue
            if attributes.get(label_name) != label_value:
                continue
            action = str(record.get("Action") or record.get("status") or "unknown")
            service = attributes.get("com.docker.compose.service") or attributes.get(
                "cft.service"
            )
            container_id = str(record.get("id") or record.get("ID") or "")
            exit_code = _integer(attributes.get("exitCode"))
            is_exit = action in {"die", "exec_die"}
            facts: dict[str, object] = {"action": action}
            if is_exit:
                facts["exit_code"] = exit_code
            events.append(
                self._event(
                    run_id=run_id,
                    kind="process_exit" if is_exit else "container_state",
                    source="docker_events",
                    level="error" if is_exit and exit_code not in {None, 0} else "info",
                    service=service,
                    container_id=container_id,
                    observed_at=_event_time(record),
                    facts=facts,
                )
            )

    def _manager_events(self, run_id: str | None) -> list[RuntimeTelemetryEvent]:
        events: list[RuntimeTelemetryEvent] = []
        for log in self.session.logs:
            if log.stage == "build":
                kind = "build_log"
            elif log.stage in {"prepare", "run", "healthcheck", "command"}:
                kind = "start_log"
            else:
                kind = "container_state"
            facts: dict[str, object] = {
                "adapter": log.adapter,
                "stage": log.stage,
                "status": log.status,
                "exit_code": log.exit_code,
                "timed_out": log.timed_out,
                "duration_ms": log.duration_ms,
            }
            if log.stdout:
                facts["stdout"] = self._safe_text(log.stdout)
            if log.stderr:
                facts["stderr"] = self._safe_text(log.stderr)
            events.append(
                self._event(
                    run_id=run_id,
                    kind=kind,
                    source="sandbox_manager",
                    level="error" if log.status in {"failed", "timed_out"} else "info",
                    service=log.service,
                    observed_at=log.captured_at,
                    facts=facts,
                )
            )
        return events

    def _ordered(
        self, events: list[RuntimeTelemetryEvent], run_id: str | None
    ) -> list[RuntimeTelemetryEvent]:
        # sorted сохраняет исходный порядок событий с одинаковым timestamp.
        ordered = sorted(events, key=lambda event: event.observed_at)
        if len(ordered) > self.max_events:
            dropped = len(ordered) - self.max_events + 1
            ordered = ordered[: self.max_events - 1]
            ordered.append(
                self._event(
                    run_id=run_id,
                    kind="collection_error",
                    source="collector",
                    level="warning",
                    facts={"operation": "limit_timeline", "dropped_events": dropped},
                )
            )
        return [event.model_copy(update={"sequence": index}) for index, event in enumerate(ordered)]

    def _event(
        self,
        *,
        run_id: str | None,
        kind: str,
        source: str,
        facts: dict[str, object],
        level: str = "info",
        service: str | None = None,
        container_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> RuntimeTelemetryEvent:
        return RuntimeTelemetryEvent(
            session_id=self.session.session_id,
            run_id=run_id,
            target=self.target_id,
            service=service,
            container_id=container_id[:12] if container_id else None,
            kind=kind,
            source=source,
            level=level,
            observed_at=observed_at or datetime.now(UTC),
            facts=facts,
        )

    def _error(
        self, operation: str, run_id: str | None, service: str | None = None
    ) -> RuntimeTelemetryEvent:
        # Фиксируем пробел в timeline, но не раскрываем пути и данные хоста.
        return self._event(
            run_id=run_id,
            kind="collection_error",
            source="collector",
            level="warning",
            service=service,
            facts={"operation": operation},
        )

    def _safe_text(self, value: str) -> str:
        redacted = redact_error_message(value, max_length=self.max_text_bytes)
        encoded = redacted.encode("utf-8")
        if len(encoded) <= self.max_text_bytes:
            return redacted
        marker = b"\n...[truncated]"
        return (encoded[: self.max_text_bytes - len(marker)] + marker).decode(
            "utf-8", errors="replace"
        )


def _split_log_line(line: str) -> tuple[datetime, str]:
    timestamp, separator, message = line.partition(" ")
    if not separator:
        return datetime.now(UTC), line
    try:
        return datetime.fromisoformat(timestamp), message
    except ValueError:
        return datetime.now(UTC), line


def _decoded_output(value: bytes, truncated: bool) -> str:
    text = value.decode("utf-8", errors="replace")
    return f"{text}\n...[truncated]" if truncated else text


def _event_time(record: dict[str, object]) -> datetime:
    raw_nanos = record.get("timeNano")
    raw_seconds = record.get("time")
    try:
        if raw_nanos is not None:
            return datetime.fromtimestamp(int(raw_nanos) / 1_000_000_000, UTC)
        if raw_seconds is not None:
            return datetime.fromtimestamp(int(raw_seconds), UTC)
    except (TypeError, ValueError, OSError):
        pass
    return datetime.now(UTC)


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _listening_ports(proc_net_tcp: str) -> list[int]:
    ports: set[int] = set()
    for line in proc_net_tcp.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 4 or columns[3] != "0A":
            continue
        try:
            ports.add(int(columns[1].rsplit(":", 1)[1], 16))
        except (IndexError, ValueError):
            continue
    return sorted(ports)
