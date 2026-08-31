"""Единый безопасный вход для запуска обнаруженных target-проектов."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Self

from executor.sandbox_policy import SandboxPolicy
from executor.sandbox_session import SandboxSession
from pipeline.cancellation import check_cancelled, suspend_cancellation
from pipeline.subprocess_runner import run_cancellable_process
from schemas.target import TargetProfile, TargetService
from security.error_redaction import redact_error_message

if TYPE_CHECKING:
    from schemas.runtime_telemetry import RuntimeTelemetryTimeline

_OUTPUT_LIMIT = 16_384
Runner = Callable[[list[str], Path, float], subprocess.CompletedProcess[str]]
HealthProbe = Callable[[str, float], bool]


class SandboxConfigurationError(RuntimeError):
    """Профиль не дает однозначного и безопасного способа запуска"""


@dataclass(frozen=True)
class SandboxLog:
    session_id: str
    adapter: str
    stage: str
    service: str | None
    argv: tuple[str, ...]
    status: str
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _bounded(value: str) -> str:
    value = redact_error_message(value, max_length=_OUTPUT_LIMIT)
    return value


def _safe_argv(argv: Sequence[str]) -> tuple[str, ...]:
    return tuple(redact_error_message(str(item), max_length=512) for item in argv)


def _run(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return run_cancellable_process(argv, cwd=cwd, timeout=timeout)


def _probe(url: str, timeout: float) -> bool:
    from executor.sandbox_session import _probe_health

    return _probe_health(url, timeout)


class _Adapter(Protocol):
    name: str

    def open(self) -> ManagedSandboxSession: ...


@dataclass
class ManagedSandboxSession:
    session_id: str
    adapter: str
    target: TargetProfile
    _start: Callable[[], None]
    _teardown: Callable[[], None]
    _state: Callable[[], dict[str, object]]
    logs: list[SandboxLog] = field(default_factory=list)
    status: str = "created"
    ready: bool = False
    _closed: bool = False

    def start(self) -> Self:
        try:
            self.status = "starting"
            self._start()
            self.status = "ready"
            self.ready = True
            return self
        except TimeoutError:
            self.status = "timed_out"
            with suspend_cancellation():
                self._teardown()
            self._closed = True
            raise
        except Exception:
            self.status = "failed"
            with suspend_cancellation():
                self._teardown()
            self._closed = True
            raise

    def collect_state(self) -> dict[str, object]:
        state = self._state()
        return {**state, "session_id": self.session_id, "adapter": self.adapter, "status": self.status, "ready": self.ready}

    def collect_telemetry(self, *, run_id: str | None = None) -> RuntimeTelemetryTimeline:
        """Собрать события только из ресурсов, принадлежащих этой сессии."""
        from executor.runtime_telemetry import RuntimeTelemetryCollector

        return RuntimeTelemetryCollector(self).collect(run_id=run_id)

    def teardown(self) -> None:
        if self._closed:
            return
        self.status = "tearing_down"
        try:
            with suspend_cancellation():
                self._teardown()
        finally:
            self._closed = True
            self.ready = False
            self.status = "closed"

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.teardown()
        return False


class _CommandAdapter:
    name = "command"

    def __init__(self, target: TargetProfile, *, runner: Runner, health_probe: HealthProbe, command_timeout: float, readiness_timeout: float) -> None:
        if target.repository_path is None:
            raise SandboxConfigurationError("TargetProfile.repository_path is required")
        self.target = target
        self.runner = runner
        self.health_probe = health_probe
        self.command_timeout = command_timeout
        self.readiness_timeout = readiness_timeout
        self.session_id = uuid.uuid4().hex
        self.logs: list[SandboxLog] = []
        self._created: list[tuple[str, list[str]]] = []

    def _command(self, stage: str, service: str | None, argv: list[str], *, cwd: Path | None = None, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        try:
            result = self.runner(argv, cwd or self.target.repository_path, self.command_timeout)
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            self.logs.append(SandboxLog(self.session_id, self.name, stage, service, _safe_argv(argv), "timed_out", "", _bounded(str(exc)), 124, True, int((time.monotonic() - started) * 1000)))
            raise TimeoutError(f"{self.name} {stage} timed out") from exc
        log = SandboxLog(self.session_id, self.name, stage, service, _safe_argv(argv), "ok" if result.returncode == 0 else "failed", _bounded(result.stdout or ""), _bounded(result.stderr or ""), result.returncode, timed_out, int((time.monotonic() - started) * 1000))
        self.logs.append(log)
        if result.returncode and not allow_failure:
            raise SandboxConfigurationError(f"{self.name} {stage} failed ({result.returncode}): {log.stderr}")
        return result

    def _wait_health(self, service: TargetService) -> None:
        if service.healthcheck is None:
            return
        url = self.target.build_url(service.healthcheck.path)
        deadline = time.monotonic() + self.readiness_timeout
        while time.monotonic() < deadline:
            check_cancelled()
            if self.health_probe(url, min(5.0, self.command_timeout)):
                self.logs.append(SandboxLog(self.session_id, self.name, "healthcheck", service.id, ("GET", service.healthcheck.path), "ok", "ready", "", 0, False, 0))
                return
            time.sleep(0.1)
        self.logs.append(SandboxLog(self.session_id, self.name, "healthcheck", service.id, ("GET", service.healthcheck.path), "timed_out", "", "Target did not become ready", 124, True, int(self.readiness_timeout * 1000)))
        raise TimeoutError("Target did not become ready")

    def _state(self) -> dict[str, object]:
        return {"resources": [kind for kind, _ in self._created], "logs": self.logs}


def _inside(repository: Path, relative_path: str, *, directory: bool = False) -> Path:
    candidate = (repository / relative_path).resolve()
    try:
        candidate.relative_to(repository.resolve())
    except ValueError as exc:
        raise SandboxConfigurationError("Target path must stay inside the repository") from exc
    if directory and not candidate.is_dir():
        raise SandboxConfigurationError(f"Target build context does not exist: {relative_path}")
    if not directory and not candidate.is_file():
        raise SandboxConfigurationError(f"Target file does not exist: {relative_path}")
    return candidate


def _one_service(target: TargetProfile, predicate: Callable[[TargetService], bool], label: str) -> TargetService:
    matches = [service for service in target.services.values() if predicate(service)]
    if len(matches) != 1:
        raise SandboxConfigurationError(f"Expected exactly one {label} service, found {len(matches)}")
    return matches[0]


class DockerComposeAdapter:
    name = "docker_compose"

    def __init__(self, target: TargetProfile, **kwargs: object) -> None:
        self.target = target
        self.kwargs = kwargs

    def open(self) -> ManagedSandboxSession:
        logs: list[SandboxLog] = []
        runner = self.kwargs.get("runner", _run)
        if not callable(runner):
            raise TypeError("runner must be callable")
        holder: dict[str, SandboxSession] = {}

        def logged_runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
            started = time.monotonic()
            try:
                result = runner(argv, cwd, timeout)
            except subprocess.TimeoutExpired as exc:
                logs.append(SandboxLog(session.session_id, self.name, "command", None, _safe_argv(argv), "timed_out", "", _bounded(str(exc)), 124, True, int((time.monotonic() - started) * 1000)))
                raise
            stage = "prepare" if argv[-1] == "config" else "run" if "up" in argv else "teardown" if "down" in argv else "state"
            logs.append(SandboxLog(session.session_id, self.name, stage, None, _safe_argv(argv), "ok" if result.returncode == 0 else "failed", _bounded(result.stdout or ""), _bounded(result.stderr or ""), result.returncode, False, int((time.monotonic() - started) * 1000)))
            return result

        session = SandboxSession(self.target, runner=logged_runner, health_probe=self.kwargs.get("health_probe", _probe), command_timeout=float(self.kwargs.get("command_timeout", 30.0)), startup_timeout=float(self.kwargs.get("startup_timeout", 300.0)), readiness_timeout=float(self.kwargs.get("readiness_timeout", 60.0)))
        holder["session"] = session
        return ManagedSandboxSession(session.session_id, self.name, self.target, session.start, lambda: session.teardown(raise_on_failure=False), session.collect_state, logs)


class DockerfileAdapter(_CommandAdapter):
    name = "dockerfile"

    def open(self) -> ManagedSandboxSession:
        service = _one_service(self.target, lambda item: bool(item.dockerfile), "Dockerfile")
        repo = self.target.repository_path
        assert repo is not None
        dockerfile = _inside(repo, service.dockerfile or "")
        context = _inside(repo, service.root, directory=True)
        image = f"cft-target-{self.session_id[:12]}"
        container = f"cft-target-{self.session_id[:12]}"
        self._created = [("container", [container]), ("image", [image])]

        def start() -> None:
            self._command("build", service.id, ["docker", "build", "--label", f"cft.session_id={self.session_id}", "--tag", image, "--file", str(dockerfile), str(context)])
            argv = [
                "docker", "run", "--detach", "--name", container,
                "--label", f"cft.session_id={self.session_id}",
                "--label", f"cft.service={service.id}",
            ]
            for address in service.allowed_local_addresses:
                host, port = _local_port(address)
                argv.extend(["--publish", f"{host}:{port}:{service.internal_port or int(port)}"])
            argv.append(image)
            self._command("run", service.id, argv)
            self._wait_health(service)

        def teardown() -> None:
            self._command("teardown", service.id, ["docker", "rm", "--force", container], allow_failure=True)
            self._command("teardown", service.id, ["docker", "image", "rm", "--force", image], allow_failure=True)

        return ManagedSandboxSession(self.session_id, self.name, self.target, start, teardown, self._state, self.logs)


class FrameworkAdapter(_CommandAdapter):
    name = "framework"

    def open(self) -> ManagedSandboxSession:
        service = _one_service(self.target, _supported_framework, "supported framework")
        if not service.build or not service.run:
            raise SandboxConfigurationError("Framework service requires profile-owned build and run argv")
        repo = self.target.repository_path
        assert repo is not None
        source = _inside(repo, service.root, directory=True)
        context = Path(tempfile.mkdtemp(prefix="cft-framework-"))
        # Исходный target копируется только во временный build context и никогда не меняется.
        shutil.copytree(source, context / "target", dirs_exist_ok=True)
        base_image = "python:3.11-slim" if "django" in service.type.lower() else "node:20-alpine"
        dockerfile = context / "Dockerfile"
        dockerfile.write_text("\n".join([f"FROM {base_image}", "WORKDIR /target", "COPY target/ /target", f"RUN {json.dumps(service.build)}", f"CMD {json.dumps(service.run)}", ""]), encoding="utf-8")
        image = f"cft-framework-{self.session_id[:12]}"
        container = f"cft-framework-{self.session_id[:12]}"
        self._created = [("container", [container]), ("image", [image])]

        def start() -> None:
            self._command("build", service.id, ["docker", "build", "--label", f"cft.session_id={self.session_id}", "--tag", image, "--file", str(dockerfile), str(context)])
            argv = [
                "docker", "run", "--detach", "--name", container,
                "--label", f"cft.session_id={self.session_id}",
                "--label", f"cft.service={service.id}",
            ]
            for address in service.allowed_local_addresses:
                host, port = _local_port(address)
                argv.extend(["--publish", f"{host}:{port}:{service.internal_port or int(port)}"])
            argv.append(image)
            self._command("run", service.id, argv)
            self._wait_health(service)

        def teardown() -> None:
            try:
                self._command("teardown", service.id, ["docker", "rm", "--force", container], allow_failure=True)
                self._command("teardown", service.id, ["docker", "image", "rm", "--force", image], allow_failure=True)
            finally:
                shutil.rmtree(context, ignore_errors=True)

        return ManagedSandboxSession(self.session_id, self.name, self.target, start, teardown, self._state, self.logs)


def _supported_framework(service: TargetService) -> bool:
    kind = service.type.lower()
    return "django" in kind or "vite" in kind or ("node" in kind and "vite" in kind)


def _local_port(address: str) -> tuple[str, str]:
    host, separator, port = address.rpartition(":")
    if not separator or not port.isdecimal() or host not in {"127.0.0.1", "localhost", "::1"}:
        raise SandboxConfigurationError("Only profile-owned loopback addresses are allowed")
    return host, port


class SandboxManager:
    """Выбирает adapter только по данным готового TargetProfile."""

    def __init__(
        self,
        *,
        policy: SandboxPolicy | None = None,
        **options: object,
    ) -> None:
        self.policy = policy
        self.options = options

    def select_adapter(self, target: TargetProfile) -> _Adapter:
        # Имя каталога не является входом выбора: это исключает угадывание target.
        if self.policy is not None:
            self.policy.validate_target_environment(target.environment)
        elif target.environment not in {"local", "sandbox", "staging"}:
            raise SandboxConfigurationError(
                f"Target environment '{target.environment}' is not allowed"
            )
        if target.runtime.type == "docker_compose":
            return DockerComposeAdapter(target, **self.options)
        if target.runtime.type == "dockerfile":
            return DockerfileAdapter(target, **_command_options(self.options))
        if target.runtime.type in {"framework", "unknown"} and any(
            _supported_framework(service) for service in target.services.values()
        ):
            return FrameworkAdapter(target, **_command_options(self.options))
        raise SandboxConfigurationError(f"Unsupported or ambiguous target runtime: {target.runtime.type}")

    def open(self, target: TargetProfile) -> ManagedSandboxSession:
        return self.select_adapter(target).open()


def _command_options(options: dict[str, object]) -> dict[str, object]:
    return {"runner": options.get("runner", _run), "health_probe": options.get("health_probe", _probe), "command_timeout": options.get("command_timeout", 30.0), "readiness_timeout": options.get("readiness_timeout", 60.0)}
