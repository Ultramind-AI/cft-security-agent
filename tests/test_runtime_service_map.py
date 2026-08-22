from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError

import pytest

from executor.runtime_service_map import ProbeResult, RuntimeServiceMapBuilder, _http_probe
from executor.sandbox_session import normalize_compose_ps
from schemas.runtime import RuntimeService
from schemas.target import TargetProfile


@dataclass
class FakeSession:
    session_id: str = "session-1"
    adapter: str = "docker_compose"
    status: str = "ready"
    state: dict[str, object] | None = None

    def collect_state(self) -> dict[str, object]:
        return self.state or {"services": []}


def _profile(**service: object) -> TargetProfile:
    return TargetProfile.model_validate({
        "id": "target",
        "environment": "sandbox",
        "services": {
            "api": {
                "type": "django",
                "root": ".",
                "internal_port": 8000,
                "runtime_endpoints": ["/ready/"],
                **service,
            }
        },
    })


def test_map_uses_compose_health_without_probe() -> None:
    called = False

    def probe(url: str, timeout: float, limit: int) -> ProbeResult:
        nonlocal called
        called = True
        return ProbeResult(False)

    session = FakeSession(state={"services": [{"Service": "api", "State": "running", "Health": "healthy"}]})
    result = RuntimeServiceMapBuilder(probe=probe).build(_profile(), session)

    service = result.services["api"]
    assert service.address == "http://api:8000"
    assert service.readiness_source == "compose_health"
    assert called is False


def test_compose_v5_health_maps_profile_id_to_compose_service() -> None:
    profile = _profile(compose_service="payments-api")
    session = FakeSession(state={
        "compose_project": "cft-sandbox-123",
        "services": [{"Service": "payments-api", "State": "running", "Status": "Up 4 seconds (healthy)"}],
    })
    result = RuntimeServiceMapBuilder().build(profile, session)

    assert result.network_name == "cft-sandbox-123_default"
    assert result.services["api"].address == "http://payments-api:8000"
    assert result.services["api"].readiness_source == "compose_health"
    assert result.services["api"].allowed_endpoints == ["/ready/"]


def test_request_host_is_kept_separate_from_internal_address() -> None:
    result = RuntimeServiceMapBuilder().build(
        _profile(compose_service="payments-api", request_host="127.0.0.1:8000"),
        FakeSession(state={"services": [{"Service": "payments-api", "State": "running", "Health": "healthy"}]}),
    )
    assert result.services["api"].address == "http://payments-api:8000"
    assert result.services["api"].request_host == "127.0.0.1:8000"


@pytest.mark.parametrize("request_host", ["http://host", "host/path", "host?x=1", "user@host", "host\r\nX: y", "host:0", "host:65536", ""])
def test_profile_rejects_unsafe_request_host(request_host: str) -> None:
    with pytest.raises(ValueError, match="request_host"):
        _profile(request_host=request_host)


def test_json_lines_compose_health_keeps_backend_ready_without_fallback() -> None:
    calls = 0

    def probe(url: str, timeout: float, limit: int) -> ProbeResult:
        nonlocal calls
        calls += 1
        return ProbeResult(False)

    state = normalize_compose_ps(
        '{"Command":"python -c \\"print(1)\\"","Service":"backend","State":"running","Health":"healthy"}\n'
        '{"Service":"frontend","State":"running","Health":"unhealthy"}'
    )
    profile = TargetProfile.model_validate({
        "id": "target", "environment": "sandbox", "services": {
            "backend": {"root": "backend", "compose_service": "backend", "internal_port": 8000, "runtime_endpoints": ["/health/"]},
            "frontend": {"root": "frontend", "compose_service": "frontend", "internal_port": 80},
        },
    })
    result = RuntimeServiceMapBuilder(probe=probe).build(profile, FakeSession(state={"services": state}))

    assert result.services["backend"].ready is True
    assert result.services["backend"].readiness_source == "compose_health"
    assert "/health/" in result.services["backend"].allowed_endpoints
    assert calls == 0


def test_service_without_healthcheck_uses_trusted_network_probe() -> None:
    calls: list[tuple[str, str]] = []

    def network_probe(network: str, url: str, request_host: str | None, timeout: float, limit: int) -> ProbeResult:
        calls.append((network, url, request_host))
        return ProbeResult(True, "network probe succeeded")

    result = RuntimeServiceMapBuilder(network_probe=network_probe).build(
        _profile(compose_service="payments-api", request_host="127.0.0.1:8000"),
        FakeSession(state={
            "compose_project": "cft-sandbox-123",
            "services": [{"Service": "payments-api", "State": "running"}],
        }),
    )

    assert result.services["api"].readiness_source == "http_probe"
    assert calls == [("cft-sandbox-123_default", "http://payments-api:8000/ready/", "127.0.0.1:8000")]


def test_fallback_probe_uses_only_profile_endpoint() -> None:
    calls: list[str] = []

    def probe(url: str, timeout: float, limit: int) -> ProbeResult:
        calls.append(url)
        return ProbeResult(url.endswith("/ready/"), "ready")

    result = RuntimeServiceMapBuilder(probe=probe).build(_profile(), FakeSession())

    assert result.services["api"].readiness_source == "http_probe"
    assert calls == ["http://api:8000/ready/"]
    assert result.services["api"].allowed_endpoints == ["/ready/"]


def test_unready_running_service_is_not_in_ready_map() -> None:
    session = FakeSession(state={"services": [{"Service": "api", "State": "running"}]})
    result = RuntimeServiceMapBuilder(probe=lambda *_: ProbeResult(False, "timeout")).build(_profile(), session)

    assert result.services == {}
    assert result.diagnostics[0].name == "api"
    assert result.diagnostics[0].diagnostic == "timeout"


def test_unhealthy_or_stopped_compose_service_is_not_ready() -> None:
    session = FakeSession(state={"services": [{"Service": "api", "State": "exited", "Health": "healthy"}]})
    result = RuntimeServiceMapBuilder(probe=lambda *_: ProbeResult(False, "not reachable")).build(_profile(), session)
    assert result.services == {}


@pytest.mark.parametrize("adapter", ["docker_compose", "dockerfile", "framework"])
def test_all_adapter_sessions_use_same_runtime_map(adapter: str) -> None:
    session = FakeSession(adapter=adapter)
    result = RuntimeServiceMapBuilder(probe=lambda *_: ProbeResult(True, "ok")).build(_profile(), session)
    assert result.session_id == session.session_id
    assert list(result.services) == ["api"]


def test_probe_error_and_timeout_remain_bounded_diagnostics() -> None:
    def failing_probe(url: str, timeout: float, limit: int) -> ProbeResult:
        raise TimeoutError("token=not-visible")

    result = RuntimeServiceMapBuilder(probe=failing_probe).build(_profile(), FakeSession())
    assert result.services == {}
    assert "not-visible" not in result.diagnostics[0].diagnostic


def test_profile_rejects_arbitrary_runtime_endpoint() -> None:
    with pytest.raises(ValueError, match="fixed absolute URL path"):
        _profile(runtime_endpoints=["https://example.com/"])


def test_map_rejects_unsafe_service_name_and_missing_internal_port() -> None:
    unsafe = _profile().services["api"].model_copy(update={"id": "api.example.com"})
    profile = _profile().model_copy(update={"services": {"api": unsafe}})
    result = RuntimeServiceMapBuilder(probe=lambda *_: ProbeResult(True)).build(profile, FakeSession())
    assert result.services == {}
    assert "not safe" in result.diagnostics[0].diagnostic

    missing_port = _profile().services["api"].model_copy(update={"internal_port": None})
    profile = _profile().model_copy(update={"services": {"api": missing_port}})
    result = RuntimeServiceMapBuilder(probe=lambda *_: ProbeResult(True)).build(profile, FakeSession())
    assert "no internal sandbox port" in result.diagnostics[0].diagnostic


@pytest.mark.parametrize(
    "address",
    ["http://127.0.0.1:8000", "http://localhost:8000", "http://example.com:8000"],
)
def test_runtime_service_rejects_host_addresses(address: str) -> None:
    with pytest.raises(ValueError, match="internal sandbox URL"):
        RuntimeService(
            name="api",
            address=address,
            ready=True,
            readiness_source="http_probe",
        )


def test_http_probe_disables_redirects(monkeypatch) -> None:
    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 302, "redirect", {}, None)

    monkeypatch.setattr("executor.runtime_service_map.build_opener", lambda _: Opener())
    result = _http_probe("http://api:8000/", 1, 64)
    assert result.ready is False
    assert result.diagnostic == "HTTP status 302"


def test_http_probe_limits_response_body(monkeypatch) -> None:
    read_sizes: list[int] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size: int) -> bytes:
            read_sizes.append(size)
            return b"x" * size

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr("executor.runtime_service_map.build_opener", lambda _: Opener())
    assert _http_probe("http://api:8000/", 1, 64).ready is True
    assert read_sizes == [65]


def test_map_does_not_break_outer_teardown_after_readiness_error() -> None:
    closed = False

    class Session(FakeSession):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            nonlocal closed
            closed = True
            return False

    with Session() as session:
        result = RuntimeServiceMapBuilder(probe=lambda *_: ProbeResult(False, "down")).build(_profile(), session)
        assert result.services == {}
    assert closed is True
