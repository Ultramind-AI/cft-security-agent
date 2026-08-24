"""Определение готовности для уже запущенного сеанса управляемой «песочницы"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pipeline.cancellation import check_cancelled
from schemas.runtime import RuntimeService, RuntimeServiceDiagnostic, RuntimeServiceMap
from schemas.target import TargetProfile, TargetService
from security.error_redaction import redact_error_message

_RESPONSE_LIMIT = 4_096
_PROBE_TIMEOUT = 2.0
_SERVICE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$")
_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[a-fA-F0-9]{64}$")
_FALLBACK_ENDPOINTS = ("/health/", "/health", "/")
_NETWORK_PROBE_PROGRAM = (
    "import sys,urllib.request; "
    "handler=type('NoRedirect',(urllib.request.HTTPRedirectHandler,),{'redirect_request':lambda *args:None})(); "
    "opener=urllib.request.build_opener(handler); "
    "headers={'Host':sys.argv[2]} if sys.argv[2] else {}; "
    "request=urllib.request.Request(sys.argv[1],headers=headers,method='GET'); "
    "response=opener.open(request,timeout=float(sys.argv[3])); "
    "response.read(int(sys.argv[4])+1); "
    "sys.exit(0 if 200<=response.status<300 else 1)"
)


@dataclass(frozen=True)
class ProbeResult:
    ready: bool
    diagnostic: str = ""


@dataclass(frozen=True)
class ComposeReadiness:
    ready: bool | None
    diagnostic: str


class ManagedSession(Protocol):
    session_id: str
    adapter: str
    status: str

    def collect_state(self) -> dict[str, object]: ...


Probe = Callable[[str, float, int], ProbeResult]
NetworkProbe = Callable[[str, str, str | None, float, int], ProbeResult]
Runner = Callable[..., subprocess.CompletedProcess[str]]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _http_probe(url: str, timeout: float, response_limit: int) -> ProbeResult:
    """Legacy injectable probe retained for isolated unit tests; never a builder default."""
    request = Request(url, method="GET", headers={"Accept": "application/json, text/plain, */*"})
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            response.read(response_limit + 1)
            if response.status < 200 or response.status >= 300:
                return ProbeResult(False, f"HTTP status {response.status}")
            return ProbeResult(True, "HTTP probe succeeded")
    except HTTPError as exc:
        return ProbeResult(False, f"HTTP status {exc.code}")
    except (OSError, URLError, TimeoutError) as exc:
        return ProbeResult(False, redact_error_message(exc, max_length=240))


class DockerNetworkProbe:
    """Perform one GET from a disposable, locked-down container in a Compose network."""

    def __init__(self, image: str, *, runner: Runner = subprocess.run) -> None:
        if not _DIGEST_IMAGE.fullmatch(image):
            raise ValueError("Network probe image must be digest-pinned")
        self.image = image
        self.runner = runner

    @classmethod
    def from_environment(cls) -> DockerNetworkProbe | None:
        image = os.getenv("CFT_SANDBOX_IMAGE", "")
        return cls(image) if _DIGEST_IMAGE.fullmatch(image) else None

    def __call__(self, network_name: str, url: str, request_host: str | None, timeout: float, response_limit: int) -> ProbeResult:
        if not _SERVICE_NAME.fullmatch(network_name.replace("-", "_")):
            return ProbeResult(False, "Trusted Compose network is unavailable")
        container = f"cft-readiness-{uuid.uuid4().hex[:12]}"
        command = [
            "docker", "run", "--detach", "--name", container, "--network", network_name,
            "--user", "65532:65532", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "8", "--memory", "64m",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=1m", self.image, "python", "-c",
            _NETWORK_PROBE_PROGRAM, url, request_host or "", str(timeout), str(response_limit),
        ]
        try:
            started = self.runner(command, capture_output=True, text=True, timeout=timeout + 5, shell=False, check=False)
            if started.returncode:
                return ProbeResult(False, redact_error_message(started.stderr or "Network probe did not start", max_length=240))
            waited = self.runner(["docker", "wait", container], capture_output=True, text=True, timeout=timeout + 5, shell=False, check=False)
            return ProbeResult(waited.returncode == 0 and waited.stdout.strip() == "0", "Sandbox-network HTTP probe succeeded" if waited.stdout.strip() == "0" else "Sandbox-network HTTP probe failed")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ProbeResult(False, redact_error_message(exc, max_length=240))
        finally:
            try:
                self.runner(["docker", "rm", "--force", container], capture_output=True, text=True, timeout=5, shell=False, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass


class RuntimeServiceMapBuilder:
    """Build a map from trusted profile facts and an active managed session."""

    def __init__(self, *, probe: Probe | None = None, network_probe: NetworkProbe | None = None, probe_timeout: float = _PROBE_TIMEOUT, response_limit: int = _RESPONSE_LIMIT) -> None:
        if probe_timeout <= 0 or response_limit < 1:
            raise ValueError("Probe timeout and response limit must be positive")
        self.probe = probe
        self.network_probe = network_probe if network_probe is not None else DockerNetworkProbe.from_environment()
        self.probe_timeout = probe_timeout
        self.response_limit = response_limit

    def build(self, profile: TargetProfile, session: ManagedSession) -> RuntimeServiceMap:
        state = session.collect_state()
        network_name = _trusted_network(state)
        services: dict[str, RuntimeService] = {}
        diagnostics: list[RuntimeServiceDiagnostic] = []
        if session.status != "ready":
            return RuntimeServiceMap(session_id=session.session_id, network_name=network_name, diagnostics=[RuntimeServiceDiagnostic(name="session", diagnostic="Session is not ready")])

        compose_readiness = _compose_readiness_by_service(state)
        for service in profile.services.values():
            check_cancelled()
            try:
                address = _sandbox_address(service)
                endpoints = _allowed_endpoints(service)
            except ValueError as exc:
                diagnostics.append(RuntimeServiceDiagnostic(name=service.id, diagnostic=str(exc)))
                continue
            compose_name = service.compose_service or service.id
            readiness = compose_readiness.get(compose_name)
            if readiness is not None and readiness.ready is True:
                services[service.id] = RuntimeService(name=service.id, type=service.type, address=address, request_host=service.request_host, ready=True, readiness_source="compose_health", allowed_endpoints=endpoints, diagnostic=readiness.diagnostic)
                continue
            if readiness is not None and readiness.ready is False:
                # Проверяем маршрут только во внутренней сети.
                if readiness.diagnostic == "Compose health is starting":
                    result = self._fallback_probe(
                        network_name, address, service.request_host, endpoints
                    )
                    if result.ready:
                        services[service.id] = RuntimeService(
                            name=service.id,
                            type=service.type,
                            address=address,
                            request_host=service.request_host,
                            ready=True,
                            readiness_source="http_probe",
                            allowed_endpoints=endpoints,
                            diagnostic=(
                                f"{readiness.diagnostic}; {result.diagnostic}"
                            ),
                        )
                    else:
                        diagnostics.append(
                            RuntimeServiceDiagnostic(
                                name=service.id,
                                diagnostic=(
                                    f"{readiness.diagnostic}; {result.diagnostic}"
                                ),
                            )
                        )
                    continue
                diagnostics.append(RuntimeServiceDiagnostic(name=service.id, diagnostic=readiness.diagnostic))
                continue
            result = self._fallback_probe(network_name, address, service.request_host, endpoints)
            if result.ready:
                services[service.id] = RuntimeService(name=service.id, type=service.type, address=address, request_host=service.request_host, ready=True, readiness_source="http_probe", allowed_endpoints=endpoints, diagnostic=result.diagnostic)
            else:
                diagnostics.append(RuntimeServiceDiagnostic(name=service.id, diagnostic=result.diagnostic))
        return RuntimeServiceMap(session_id=session.session_id, network_name=network_name, services=services, diagnostics=diagnostics)

    def _fallback_probe(self, network_name: str | None, address: str, request_host: str | None, endpoints: list[str]) -> ProbeResult:
        if self.probe is not None:
            return _probe_endpoints(self.probe, address, endpoints, self.probe_timeout, self.response_limit)
        if self.network_probe is None:
            return ProbeResult(False, "Sandbox-network probe is unavailable: configure a digest-pinned CFT_SANDBOX_IMAGE")
        if network_name is None:
            return ProbeResult(False, "Trusted Compose network is unavailable")
        last = ProbeResult(False, "No readiness endpoint succeeded")
        for endpoint in endpoints:
            result = self.network_probe(network_name, f"{address}{endpoint}", request_host, self.probe_timeout, self.response_limit)
            if result.ready:
                return result
            last = result
        return last


def _trusted_network(state: dict[str, object]) -> str | None:
    project = state.get("compose_project")
    if not isinstance(project, str) or not _SERVICE_NAME.fullmatch(project.replace("-", "_")):
        return None
    return f"{project}_default"


def _compose_readiness_by_service(state: dict[str, object]) -> dict[str, ComposeReadiness]:
    raw_services = state.get("services", [])
    if not isinstance(raw_services, list):
        return {}
    result: dict[str, ComposeReadiness] = {}
    for item in raw_services:
        if not isinstance(item, dict):
            continue
        name = item.get("Service") or item.get("service")
        if not isinstance(name, str):
            continue
        state_value = _text(item.get("State") or item.get("state"))
        health_value = _text(item.get("Health") or item.get("health"))
        status_value = _text(item.get("Status") or item.get("status"))
        if state_value is None and status_value is not None:
            state_value = _state_from_status(status_value)
        if health_value is None and status_value is not None:
            health_value = _health_from_status(status_value)
        if state_value != "running":
            result[name] = ComposeReadiness(False, f"Compose state is {state_value}")
        elif health_value == "healthy":
            result[name] = ComposeReadiness(True, "Compose healthcheck is healthy")
        elif health_value is not None:
            result[name] = ComposeReadiness(False, f"Compose health is {health_value}")
    return result


def _text(value: object) -> str | None:
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _health_from_status(status: str) -> str | None:
    if "(healthy)" in status:
        return "healthy"
    if "(unhealthy)" in status:
        return "unhealthy"
    if "(starting)" in status:
        return "starting"
    return None


def _state_from_status(status: str) -> str | None:
    return "running" if status.startswith(("up", "running")) else None


def _sandbox_address(service: TargetService) -> str:
    hostname = service.compose_service or service.id
    if not _SERVICE_NAME.fullmatch(hostname):
        raise ValueError("Compose service name is not safe for a sandbox address")
    if service.internal_port is None:
        raise ValueError("Profile has no internal sandbox port for service")
    return f"http://{hostname}:{service.internal_port}"


def _allowed_endpoints(service: TargetService) -> list[str]:
    values = []
    if service.healthcheck is not None:
        values.append(service.healthcheck.path)
    values.extend(service.runtime_endpoints)
    values.extend(path for path in _FALLBACK_ENDPOINTS if not values)
    return list(dict.fromkeys(values))


def _probe_endpoints(probe: Probe, address: str, endpoints: list[str], timeout: float, response_limit: int) -> ProbeResult:
    last = ProbeResult(False, "No readiness endpoint succeeded")
    for endpoint in endpoints:
        try:
            result = probe(f"{address}{endpoint}", timeout, response_limit)
        except (OSError, TimeoutError, ValueError) as exc:
            result = ProbeResult(False, redact_error_message(exc, max_length=240))
        if result.ready:
            return result
        last = result
    return last
