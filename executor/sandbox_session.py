"""Lifecycle management for one trusted Docker Compose target session."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Self
from urllib.error import URLError
from urllib.request import urlopen

import yaml

from pipeline.cancellation import RunCancelled, check_cancelled, suspend_cancellation
from pipeline.subprocess_runner import run_cancellable_process
from schemas.target import TargetProfile


class SessionStatus(str, Enum):
    CREATED = "created"
    PREPARING = "preparing"
    STARTING = "starting"
    READY = "ready"
    TEARING_DOWN = "tearing_down"
    CLOSED = "closed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class SandboxSessionError(RuntimeError):
    pass


class SessionTimeoutError(SandboxSessionError, TimeoutError):
    pass


class SessionCleanupError(SandboxSessionError):
    pass


@dataclass
class SandboxSessionInfo:
    session_id: str
    compose_project: str
    target_path: Path
    compose_file: Path
    working_directory: Path
    status: SessionStatus = SessionStatus.CREATED
    started_at: float | None = None
    services: list[dict[str, object]] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[list[str], Path, float], subprocess.CompletedProcess[str]]
HealthProbe = Callable[[str, float], bool]


def _bounded(text: str, limit: int = 16_384) -> str:
    return text[:limit] + ("\n...[truncated]" if len(text) > limit else "")


def normalize_compose_ps(stdout: str) -> list[dict[str, object]]:
    """Normalize Compose JSON arrays, single objects, and JSON Lines."""
    text = stdout.strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        records: list[dict[str, object]] = []
        # Compose v5 can emit one JSON object per line; a bad line must not hide its neighbours.
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                records.append({"raw": _bounded(line, 1_024)})
                continue
            records.extend(_normalize_compose_records(decoded))
        return records
    return _normalize_compose_records(decoded)


def _normalize_compose_records(decoded: object) -> list[dict[str, object]]:
    values = decoded if isinstance(decoded, list) else [decoded]
    records: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, dict):
            records.append({"raw": _bounded(str(value), 1_024)})
            continue
        normalized: dict[str, object] = {}
        for key in ("Service", "Name", "State", "Health", "Status", "ID"):
            found = _compose_field(value, key)
            if found is not None:
                normalized[key] = found
        records.append(normalized)
    return records


def _compose_field(record: dict[object, object], name: str) -> object | None:
    expected = name.lower()
    for key, value in record.items():
        if isinstance(key, str) and key.lower() == expected:
            return value
    return None


def _run_command(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    # Команда собирается только из доверенного TargetProfile и фиксированных аргументов.
    return run_cancellable_process(argv, cwd=cwd, timeout=timeout)


def _probe_health(url: str, timeout: float) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:  # nosec B310: URL is target-owned
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


class SandboxSession:
    """Start and always remove one Compose project owned by this session."""

    def __init__(
        self,
        target: TargetProfile,
        *,
        working_root: str | Path | None = None,
        readiness_paths: Sequence[str] | None = None,
        command_timeout: float = 30.0,
        startup_timeout: float = 300.0,
        readiness_timeout: float = 60.0,
        readiness_interval: float = 0.5,
        runner: Runner = _run_command,
        health_probe: HealthProbe = _probe_health,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if target.repository_path is None or target.runtime.compose_file is None:
            raise ValueError("SandboxSession requires target repository_path and runtime.compose_file")
        compose_file = target.compose_file_path()
        configured_readiness_paths = tuple(readiness_paths or target.healthcheck_paths())
        if not configured_readiness_paths:
            raise ValueError("SandboxSession requires at least one configured service healthcheck")
        self.target = target
        self._readiness_paths = configured_readiness_paths
        if command_timeout <= 0 or startup_timeout <= 0 or readiness_timeout <= 0:
            raise ValueError("Sandbox session timeouts must be positive")
        self.command_timeout = command_timeout
        self.startup_timeout = startup_timeout
        self.readiness_timeout = readiness_timeout
        self.readiness_interval = readiness_interval
        self._runner = runner
        self._health_probe = health_probe
        self._clock = clock
        self._sleep = sleep
        root = Path(working_root) if working_root else None
        workdir = Path(tempfile.mkdtemp(prefix="cft-sandbox-session-", dir=root))
        session_id = uuid.uuid4().hex
        self.info = SandboxSessionInfo(
            session_id=session_id,
            compose_project=f"cft-sandbox-{session_id[:12]}",
            target_path=target.repository_path,
            compose_file=compose_file,
            working_directory=workdir,
        )
        self._compose_override_file = workdir / "compose.sandbox.override.yml"
        self._torn_down = False

    @property
    def session_id(self) -> str:
        return self.info.session_id

    @property
    def compose_project(self) -> str:
        return self.info.compose_project

    @property
    def status(self) -> SessionStatus:
        return self.info.status

    def _base_compose(self, *arguments: str) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-name",
            self.compose_project,
            "--file",
            str(self.info.compose_file),
            *arguments,
        ]

    def _compose(self, *arguments: str) -> list[str]:
        command = self._base_compose()
        if self._compose_override_file.is_file():
            command.extend(["--file", str(self._compose_override_file)])
        return [*command, *arguments]

    def _command(
        self,
        argv: list[str],
        *,
        allow_failure: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command_timeout = self.command_timeout if timeout is None else timeout
        try:
            result = self._runner(argv, self.info.target_path, command_timeout)
        except subprocess.TimeoutExpired as exc:
            self.info.stderr = _bounded(f"Command timed out: {' '.join(argv)}\n{exc}")
            raise SessionTimeoutError(self.info.stderr) from exc
        self.info.stdout = _bounded(result.stdout or "")
        self.info.stderr = _bounded(result.stderr or "")
        if result.returncode and not allow_failure:
            raise SandboxSessionError(
                f"Docker Compose command failed ({result.returncode}): {' '.join(argv)}\n{self.info.stderr}"
            )
        return result

    def prepare(self) -> None:
        self.info.status = SessionStatus.PREPARING
        if not self.info.target_path.is_dir():
            raise SandboxSessionError(f"Trusted target directory does not exist: {self.info.target_path}")
        if not self.info.compose_file.is_file():
            raise SandboxSessionError(f"Trusted Compose file does not exist: {self.info.compose_file}")
        source_config = self._command(self._base_compose("config"))
        document = self._validate_compose_config(source_config.stdout)
        self._write_compose_override(document)
        effective_config = self._command(self._compose("config"))
        self._validate_compose_config(effective_config.stdout, reject_host_ports=True)

    @staticmethod
    def _validate_compose_config(
        config: str,
        *,
        reject_host_ports: bool = False,
    ) -> dict[str, object]:
        try:
            document = yaml.safe_load(config) or {}
        except yaml.YAMLError as exc:
            raise SandboxSessionError("docker compose config returned invalid YAML") from exc
        if not isinstance(document, dict):
            raise SandboxSessionError("docker compose config must be a mapping")
        # Внешние ресурсы и bind-mount нельзя надёжно привязать к session_id и удалить.
        for kind in ("networks", "volumes"):
            for name, definition in (document.get(kind, {}) or {}).items():
                if isinstance(definition, dict) and definition.get("external"):
                    raise SandboxSessionError(f"Unsafe external Compose {kind[:-1]}: {name}")
        for service, definition in (document.get("services", {}) or {}).items():
            if not isinstance(definition, dict):
                raise SandboxSessionError(f"Invalid Compose service definition: {service}")
            if definition.get("network_mode") == "host":
                raise SandboxSessionError(f"Unsafe host network mode in service: {service}")
            if reject_host_ports and definition.get("ports"):
                raise SandboxSessionError(f"Host port publishing is disabled in sandbox: {service}")
            for volume in (definition or {}).get("volumes", []) or []:
                if isinstance(volume, dict) and volume.get("type") == "bind":
                    raise SandboxSessionError(f"Unsafe host bind mount in service: {service}")
        return document

    def _write_compose_override(self, document: dict[str, object]) -> None:
        services = document.get("services", {}) or {}
        if not isinstance(services, dict) or not services:
            raise SandboxSessionError("Docker Compose target has no services")
        if len(services) > 64:
            raise SandboxSessionError("Docker Compose target has too many services")
        lines = ["services:"]
        for service in sorted(str(name) for name in services):
            lines.extend(
                (
                    f"  {json.dumps(service)}:",
                    "    ports: !reset []",
                )
            )
        self._compose_override_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def start(self) -> SandboxSession:
        try:
            self.prepare()
            self.info.status = SessionStatus.STARTING
            self.info.started_at = self._clock()
            self._command(
                self._compose("up", "--build", "--detach"),
                timeout=self.startup_timeout,
            )
            self._wait_until_ready()
            self.info.status = SessionStatus.READY
            self.collect_state()
            return self
        except RunCancelled:
            self.info.status = SessionStatus.FAILED
            self.teardown(raise_on_failure=False)
            raise
        except SessionTimeoutError:
            self.info.status = SessionStatus.TIMED_OUT
            self.teardown(raise_on_failure=False)
            raise
        except Exception:
            self.info.status = SessionStatus.FAILED
            self.teardown(raise_on_failure=False)
            raise

    def _wait_until_ready(self) -> None:
        urls = [self.target.build_url(path) for path in self._readiness_paths] if self.target.runtime.base_url else []
        deadline = self._clock() + self.readiness_timeout
        while self._clock() < deadline:
            check_cancelled()
            if self._compose_services_healthy():
                return
            if urls and all(
                self._health_probe(url, min(5.0, self.command_timeout))
                for url in urls
            ):
                return
            self._sleep(self.readiness_interval)
        raise SessionTimeoutError(
            "Target did not become ready within "
            f"{self.readiness_timeout}s: {', '.join(urls)}"
        )

    def _compose_services_healthy(self) -> bool:
        """Compose health принадлежит sandbox-сессии и не требует host URL."""
        result = self._command(self._compose("ps", "--format", "json"), allow_failure=True)
        services = normalize_compose_ps(result.stdout or "")
        expected = {service.compose_service or service.id for service in self.target.services.values()}
        by_name = {
            str(item.get("Service") or item.get("service")): item
            for item in services
        }
        if not expected or not expected.issubset(by_name):
            return False
        return all(
            str(by_name[name].get("State") or by_name[name].get("state", "")).lower()
            == "running"
            and str(by_name[name].get("Health") or by_name[name].get("health", "")).lower()
            == "healthy"
            for name in expected
        )

    def collect_state(self) -> dict[str, object]:
        if self._torn_down:
            services: list[dict[str, object]] = []
        else:
            result = self._command(self._compose("ps", "--format", "json"), allow_failure=True)
            services = normalize_compose_ps(result.stdout or "")
            self.info.services = services
        duration = 0.0 if self.info.started_at is None else round(self._clock() - self.info.started_at, 3)
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "compose_project": self.compose_project,
            "services": self.info.services if not self._torn_down else services,
            "ready": self.status == SessionStatus.READY,
            "diagnostic": self.info.stderr or self.info.stdout,
            "duration_seconds": duration,
        }

    def _labelled_resources(self, resource_type: str) -> list[str]:
        label_filter = f"label=com.docker.compose.project={self.compose_project}"
        commands = {
            "container": ["docker", "ps", "--all", "--quiet", "--filter", label_filter],
            "network": ["docker", "network", "ls", "--quiet", "--filter", label_filter],
            "volume": ["docker", "volume", "ls", "--quiet", "--filter", label_filter],
        }
        try:
            command = commands[resource_type]
        except KeyError as exc:
            raise ValueError(f"Unknown Docker resource type: {resource_type}") from exc
        result = self._command(command, allow_failure=True)
        if result.returncode:
            raise SessionCleanupError(
                f"Cannot verify Docker {resource_type} cleanup: {self.info.stderr}"
            )
        return [line for line in result.stdout.splitlines() if line]

    def teardown(self, *, raise_on_failure: bool = True) -> None:
        if self._torn_down:
            return
        # После cancel обязательный teardown выполняется без проверки токена.
        with suspend_cancellation():
            self._teardown_without_cancellation(raise_on_failure=raise_on_failure)

    def _teardown_without_cancellation(self, *, raise_on_failure: bool) -> None:
        self.info.status = SessionStatus.TEARING_DOWN
        verification_errors: list[str] = []
        try:
            down = self._command(
                self._compose("down", "--volumes", "--remove-orphans"),
                allow_failure=True,
            )
            fallback_required = bool(down.returncode)
        except SandboxSessionError:
            fallback_required = True

        if fallback_required:
            # Резервная очистка выбирает ресурсы только по точной метке Compose-проекта.
            removals = (
                ("container", ["docker", "rm", "-f"]),
                ("network", ["docker", "network", "rm"]),
                ("volume", ["docker", "volume", "rm"]),
            )
            for resource_type, removal in removals:
                try:
                    resource_ids = self._labelled_resources(resource_type)
                    if resource_ids:
                        self._command([*removal, *resource_ids], allow_failure=True)
                except SandboxSessionError as exc:
                    verification_errors.append(str(exc))

        leftovers: dict[str, list[str]] = {}
        for kind in ("container", "network", "volume"):
            try:
                leftovers[kind] = self._labelled_resources(kind)
            except SandboxSessionError as exc:
                verification_errors.append(str(exc))
        self._torn_down = True
        shutil.rmtree(self.info.working_directory, ignore_errors=True)
        remaining = {kind: names for kind, names in leftovers.items() if names}
        if remaining or verification_errors:
            self.info.status = SessionStatus.FAILED
            error = SessionCleanupError(
                "Sandbox cleanup could not be confirmed: "
                f"remaining={remaining}, verification_errors={verification_errors}"
            )
            if raise_on_failure:
                raise error
            return
        self.info.status = SessionStatus.CLOSED

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.teardown(raise_on_failure=exc_type is None)
        return False
