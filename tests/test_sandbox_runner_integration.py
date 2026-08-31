"""Реальная цепочка SberLab runner, вне lab с Docker тест пропускаем"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from discovery.service import ProjectDiscovery
from executor.approvals import InMemoryApprovalStore
from executor.executor import SafeExecutor
from executor.runtime_service_map import RuntimeServiceMapBuilder
from executor.sandbox_manager import SandboxManager
from executor.sandbox_runner import SandboxRunner, _bounded_output
from schemas.action import ActionProposal
from schemas.runtime import RuntimeServiceMap
from schemas.target import TargetProfile
from schemas.validation import ValidationResult


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False).returncode == 0
    except OSError:
        return False


def _profile() -> TargetProfile:
    source = os.getenv("SBERLAB_TARGET_PATH")
    if not source:
        pytest.skip("Set SBERLAB_TARGET_PATH to run SberLab runner integration")
    path = Path(source).resolve()
    if not (path / "docker-compose.yml").is_file():
        pytest.skip("SBERLAB_TARGET_PATH has no docker-compose.yml")
    manifest = Path(__file__).resolve().parents[1] / "targets" / "sberlab.yaml"
    base = TargetProfile.from_yaml(
        manifest,
        repository_path_override=path,
        base_url_override=os.getenv("SBERLAB_BASE_URL", "http://127.0.0.1:8000"),
    )
    return ProjectDiscovery().build_profile(ProjectDiscovery().discover(path), base_profile=base)


def _runtime_diagnostic(profile: TargetProfile, runtime_map: RuntimeServiceMap, state: dict[str, object]) -> str:
    trusted_endpoints = {name: service.runtime_endpoints for name, service in profile.services.items()}
    readiness = {name: service.readiness_source for name, service in runtime_map.services.items()}
    return (
        f"runtime_keys={sorted(runtime_map.services)}; profile_services={sorted(profile.services)}; "
        f"trusted_endpoints={trusted_endpoints!r}; "
        f"compose_services={state.get('services', [])!r:.200}; "
        f"readiness={readiness!r}; network={runtime_map.network_name!r}; "
        f"diagnostics={[item.model_dump() for item in runtime_map.diagnostics][:8]!r:.1000}"
    )


def _action_diagnostic(result: object) -> str:
    rows = []
    for item in getattr(result, "results", []):
        rows.append({
            "action_id": item.action_id, "tool": item.tool, "service": item.service,
            "status": item.status, "exit_code": item.exit_code, "timed_out": item.timed_out,
            "stdout": _bounded_output(item.stdout), "stderr": _bounded_output(item.stderr),
            "runtime_instance_id": item.runtime_instance_id, "evidence_ref": item.evidence_ref,
        })
    return _bounded_output(repr(rows))


def _policy(path: Path, image: str) -> Path:
    policy = path / "runner-policy.yaml"
    policy.write_text(
        "\n".join([
            "runtime:", "  backend: docker", "  network_mode: internal_bridge",
            f"  sandbox_image: {image}", "  internal_network: cft_internal_security_net",
            "environments:", "  allowed: [local, sandbox, staging]",
            "limits:", "  executor:", "    wall_time_seconds: 10", "    cpu_time_seconds: 2",
            "    memory_mb: 256", "    max_file_bytes: 1048576", "    max_processes: 8",
            "    max_output_bytes: 16384", "    max_runs_per_action: 1", "    max_concurrent_runs: 1",
        ]), encoding="utf-8")
    return policy


@pytest.mark.integration
@pytest.mark.skipif(not _docker_ready(), reason="requires a working Docker daemon")
def test_sberlab_approved_runner_sequence_uses_manager_runtime_map_and_audit(tmp_path: Path) -> None:
    image = os.getenv("CFT_SANDBOX_IMAGE")
    if not image or "@sha256:" not in image:
        pytest.skip("Set CFT_SANDBOX_IMAGE to an immutable digest-pinned image")
    profile = _profile()
    approvals = InMemoryApprovalStore()
    executor = SafeExecutor.from_config(
        approvals=approvals,
        policy_file=_policy(tmp_path, image),
        target_profile=profile,
        evidence_directory=tmp_path / "evidence",
        audit_log_path=tmp_path / "audit.jsonl",
        workspace_directory=tmp_path / "workspace",
        backend_override="docker",
    )
    assert isinstance(executor._runner, SandboxRunner)
    session = SandboxManager(readiness_timeout=90).open(profile)
    with session:
        runtime_map = RuntimeServiceMapBuilder().build(profile, session)
        state = session.collect_state()
        backend = runtime_map.services.get("backend")
        diagnostic = _runtime_diagnostic(profile, runtime_map, state)
        assert backend is not None, diagnostic
        assert backend.ready is True, diagnostic
        assert "/health/" in backend.allowed_endpoints, diagnostic
        assert "/api/projects/" in backend.allowed_endpoints, diagnostic
        assert backend.address.startswith("http://") and "127.0.0.1" not in backend.address and "localhost" not in backend.address, diagnostic
        assert backend.request_host == "127.0.0.1:8000", diagnostic
        assert runtime_map.network_name, diagnostic
        assert "token=" not in diagnostic.lower() and "password=" not in diagnostic.lower(), diagnostic
        actions = [
            ActionProposal(id="runner-health", tool="observe_http_surface", target=profile.id, purpose="health", expected_evidence="health", service="backend", endpoint="/health/"),
            ActionProposal(id="runner-projects", tool="observe_http_surface", target=profile.id, purpose="projects", expected_evidence="projects", service="backend", endpoint="/api/projects/"),
        ]
        for action in actions:
            approvals.record(action, ValidationResult(approved=True, action_id=action.id, reason="integration"))
        result = executor.execute_sequence(actions, runtime_services=runtime_map)
        diagnostic = _action_diagnostic(result)
        assert result.status == "completed", diagnostic
        assert [item.action_id for item in result.results] == ["runner-health", "runner-projects"]
        assert {item.run_id for item in result.results} == {result.run_id}
        assert result.runtime_instance_id
        assert {item.runtime_instance_id for item in result.results} == {result.runtime_instance_id}
        assert all(item.session_id == session.session_id for item in result.results)
        assert all(item.evidence_ref.startswith("execution-") for item in result.results)
    workspace = tmp_path / "workspace"
    assert not workspace.exists() or not list(workspace.iterdir())
