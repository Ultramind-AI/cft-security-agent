from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from executor.sandbox_session import SandboxSession, SessionTimeoutError
from executor.targets import TargetDefinition


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False).returncode == 0
    except OSError:
        return False


def _target() -> TargetDefinition:
    path = Path(os.environ["SBERLAB_TARGET_PATH"]).resolve()
    compose = next((candidate for candidate in (path / "docker-compose.yml", path / "compose.yml") if candidate.is_file()), None)
    if compose is None:
        pytest.skip("SBERLAB_TARGET_PATH has no docker-compose.yml or compose.yml")
    return TargetDefinition(
        id="sberlab-local",
        environment="local",
        base_url=os.environ.get("SBERLAB_BASE_URL", "http://127.0.0.1:8000"),
        repository_path=path,
        compose_file=compose,
        health_path="/health/",
    )


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_ready(), reason="SberLab integration requires a working Docker daemon"),
    pytest.mark.skipif(not os.getenv("SBERLAB_TARGET_PATH"), reason="Set SBERLAB_TARGET_PATH to run SberLab integration"),
]


def _resources(project: str, kind: str) -> list[str]:
    commands = {
        "container": ["docker", "ps", "--all", "--quiet"],
        "network": ["docker", "network", "ls", "--quiet"],
        "volume": ["docker", "volume", "ls", "--quiet"],
    }
    result = subprocess.run(
        [
            *commands[kind],
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        shell=False,
        check=True,
    )
    return result.stdout.splitlines()


def test_sberlab_session_becomes_ready_and_removes_resources(tmp_path: Path) -> None:
    session = SandboxSession(_target(), working_root=tmp_path, readiness_timeout=90)
    project = session.compose_project
    with session:
        state = session.collect_state()
        assert state["ready"] is True
        assert state["services"]
    assert all(
        not _resources(project, kind)
        for kind in ("container", "network", "volume")
    )


def test_sberlab_session_tears_down_after_controlled_exception(tmp_path: Path) -> None:
    session = SandboxSession(_target(), working_root=tmp_path, readiness_timeout=90)
    project = session.compose_project
    with pytest.raises(RuntimeError, match="controlled"), session:
        raise RuntimeError("controlled")
    assert all(
        not _resources(project, kind)
        for kind in ("container", "network", "volume")
    )


def test_sberlab_session_tears_down_after_timeout(tmp_path: Path) -> None:
    session = SandboxSession(_target(), working_root=tmp_path, readiness_timeout=0.01, health_probe=lambda url, timeout: False)
    project = session.compose_project
    with pytest.raises(SessionTimeoutError):
        session.start()
    assert all(
        not _resources(project, kind)
        for kind in ("container", "network", "volume")
    )
