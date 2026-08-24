"""Optional real-target checks; each target is discovered before it is started."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from discovery.service import ProjectDiscovery
from evidence.runtime import build_http_surface_evidence
from evidence.store import JsonExecutionEvidenceStore
from executor.approvals import InMemoryApprovalStore
from executor.executor import SafeExecutor
from executor.runtime_service_map import RuntimeServiceMapBuilder
from executor.sandbox_manager import SandboxManager
from schemas.action import ActionProposal
from schemas.target import TargetProfile
from schemas.validation import ValidationResult


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False).returncode == 0
    except OSError:
        return False


def _profile(path_variable: str, _base_url_variable: str) -> TargetProfile:
    raw_path = os.getenv(path_variable)
    if not raw_path:
        pytest.skip(f"Set {path_variable} to run this integration test")
    repository = Path(raw_path).resolve()
    if not repository.is_dir():
        pytest.skip(f"{path_variable} is not an existing directory")
    profile_name = {
        "SBERLAB_TARGET_PATH": "sberlab.yaml",
        "AUTODEALER_TARGET_PATH": "autodealer.yaml",
    }[path_variable]
    base = TargetProfile.from_yaml(
        Path("targets") / profile_name,
        repository_path_override=repository,
    )
    profile = ProjectDiscovery().build_profile(ProjectDiscovery().discover(repository), base_profile=base)
    if profile.runtime.type != "docker_compose" or not profile.runtime.compose_file:
        pytest.skip("Discovery did not produce an unambiguous Compose runtime")
    if not profile.healthcheck_paths():
        pytest.skip("Discovery/profile has no healthcheck for safe readiness")
    return profile


def _resources(project: str, kind: str) -> list[str]:
    commands = {
        "container": ["docker", "ps", "--all", "--quiet"],
        "network": ["docker", "network", "ls", "--quiet"],
        "volume": ["docker", "volume", "ls", "--quiet"],
    }
    result = subprocess.run([*commands[kind], "--filter", f"label=com.docker.compose.project={project}"], capture_output=True, text=True, timeout=15, check=True, shell=False)
    return result.stdout.splitlines()


def _runtime_policy(path: Path, image: str) -> Path:
    policy = path / "runtime-observation-policy.yaml"
    policy.write_text(
        "\n".join(
            [
                "runtime:",
                "  backend: docker",
                "  network_mode: internal_bridge",
                f"  sandbox_image: {image}",
                "  internal_network: cft_internal_security_net",
                "environments:",
                "  allowed: [local, sandbox, staging]",
                "limits:",
                "  executor:",
                "    wall_time_seconds: 10",
                "    cpu_time_seconds: 2",
                "    memory_mb: 256",
                "    max_file_bytes: 1048576",
                "    max_processes: 8",
                "    max_output_bytes: 16384",
                "    max_runs_per_action: 1",
                "    max_concurrent_runs: 1",
            ]
        ),
        encoding="utf-8",
    )
    return policy


@pytest.mark.integration
@pytest.mark.skipif(not _docker_ready(), reason="requires a working Docker daemon")
@pytest.mark.parametrize(
    ("path_variable", "base_url_variable"),
    [("SBERLAB_TARGET_PATH", "SBERLAB_BASE_URL"), ("AUTODEALER_TARGET_PATH", "AUTODEALER_BASE_URL")],
)
def test_discovered_target_runs_through_manager_and_is_torn_down(
    path_variable: str,
    base_url_variable: str,
) -> None:
    profile = _profile(path_variable, base_url_variable)
    session = SandboxManager(readiness_timeout=90).open(profile)
    project = session.collect_state().get("compose_project")
    with session:
        assert session.ready is True
        assert session.status == "ready"
        assert session.logs
        runtime_map = RuntimeServiceMapBuilder().build(profile, session)
        assert runtime_map.session_id == session.session_id
    assert isinstance(project, str)
    assert all(not _resources(project, kind) for kind in ("container", "network", "volume"))


@pytest.mark.integration
@pytest.mark.skipif(not _docker_ready(), reason="requires a working Docker daemon")
@pytest.mark.parametrize(
    ("path_variable", "base_url_variable"),
    [
        ("SBERLAB_TARGET_PATH", "SBERLAB_BASE_URL"),
        ("AUTODEALER_TARGET_PATH", "AUTODEALER_BASE_URL"),
    ],
)
def test_runtime_http_observations_are_persisted_for_each_discovered_target(
    path_variable: str,
    base_url_variable: str,
    tmp_path: Path,
) -> None:
    """Проверка использует реальные ответы target."""
    image = os.getenv("CFT_SANDBOX_IMAGE", "")
    if "@sha256:" not in image:
        pytest.skip("Set CFT_SANDBOX_IMAGE to an immutable digest-pinned image")
    profile = _profile(path_variable, base_url_variable)
    service = next(
        (item for item in profile.services.values() if item.healthcheck is not None),
        None,
    )
    if service is None or service.healthcheck is None:
        pytest.skip("Discovered target has no service healthcheck for a bounded GET")

    action = ActionProposal(
        id=f"runtime-observe-{profile.id}",
        tool="observe_http_surface",
        target=profile.id,
        environment=profile.environment,
        service=service.id,
        endpoint=service.healthcheck.path,
        purpose="Collect bounded HTTP runtime observations from the ready health route.",
        expected_evidence="Seven structured HTTP runtime observations.",
    )
    approvals = InMemoryApprovalStore()
    approvals.record(
        action,
        ValidationResult(approved=True, action_id=action.id, reason="integration"),
    )
    evidence_dir = tmp_path / "evidence"
    executor = SafeExecutor.from_config(
        approvals=approvals,
        policy_file=_runtime_policy(tmp_path, image),
        target_profile=profile,
        evidence_directory=evidence_dir,
        audit_log_path=tmp_path / "audit.jsonl",
        workspace_directory=tmp_path / "workspace",
        backend_override="docker",
    )

    execution = executor.execute(action)
    assert execution.status == "completed", execution.stderr
    record = JsonExecutionEvidenceStore(evidence_dir).get_execution(execution.evidence_ref)
    evidence = build_http_surface_evidence(
        action=action,
        execution=execution,
        record=record,
        artifact_refs=[execution.evidence_ref, execution.audit_ref, *execution.artifacts],
        hypothesis_id="integration-runtime-observation",
    )

    assert len(evidence) == 7
    assert {item.type for item in evidence} == {
        "http_status",
        "http_security_headers",
        "http_cookie_attributes",
        "http_cors",
        "http_redirect",
        "http_health_or_error",
        "http_route_access",
    }
    assert all(item.source == "runtime" for item in evidence)
    assert all(item.sandbox_session_id == record["session_id"] for item in evidence)
    assert all(item.artifacts for item in evidence)
