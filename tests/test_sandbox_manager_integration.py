"""Optional real-target checks; each target is discovered before it is started."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from discovery.service import ProjectDiscovery
from executor.runtime_service_map import RuntimeServiceMapBuilder
from executor.sandbox_manager import SandboxManager
from schemas.target import TargetProfile


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False).returncode == 0
    except OSError:
        return False


def _profile(path_variable: str, base_url_variable: str) -> TargetProfile:
    raw_path = os.getenv(path_variable)
    if not raw_path:
        pytest.skip(f"Set {path_variable} to run this integration test")
    repository = Path(raw_path).resolve()
    if not repository.is_dir():
        pytest.skip(f"{path_variable} is not an existing directory")
    base = TargetProfile.model_validate({
        "id": f"integration-{path_variable.lower()}",
        "repository_path": str(repository),
        "environment": "local",
        "runtime": {"base_url": os.getenv(base_url_variable, "") or None},
    })
    profile = ProjectDiscovery().build_profile(ProjectDiscovery().discover(repository), base_profile=base)
    if profile.runtime.type != "docker_compose" or not profile.runtime.compose_file:
        pytest.skip("Discovery did not produce an unambiguous Compose runtime")
    if not profile.runtime.base_url or not profile.healthcheck_paths():
        pytest.skip("Discovery/profile has no base URL and healthcheck for safe readiness")
    return profile


def _resources(project: str, kind: str) -> list[str]:
    commands = {
        "container": ["docker", "ps", "--all", "--quiet"],
        "network": ["docker", "network", "ls", "--quiet"],
        "volume": ["docker", "volume", "ls", "--quiet"],
    }
    result = subprocess.run([*commands[kind], "--filter", f"label=com.docker.compose.project={project}"], capture_output=True, text=True, timeout=15, check=True, shell=False)
    return result.stdout.splitlines()


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
