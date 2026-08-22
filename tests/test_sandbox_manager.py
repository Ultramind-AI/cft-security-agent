from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from executor.sandbox_manager import (
    DockerComposeAdapter,
    DockerfileAdapter,
    FrameworkAdapter,
    SandboxConfigurationError,
    SandboxManager,
)
from schemas.target import TargetProfile


class FakeRunner:
    def __init__(self, *, timeout_on: str | None = None) -> None:
        self.commands: list[list[str]] = []
        self.timeout_on = timeout_on

    def __call__(self, argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        if self.timeout_on and self.timeout_on in argv:
            raise subprocess.TimeoutExpired(argv, timeout)
        if argv[-1] == "config":
            return subprocess.CompletedProcess(argv, 0, "services:\n  app: {}\n", "")
        if "ps" in argv:
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        return subprocess.CompletedProcess(argv, 0, "token=secret-value", "",)


def _profile(tmp_path: Path, runtime: str, service_type: str, **service: object) -> TargetProfile:
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    return TargetProfile.model_validate({
        "id": "target", "repository_path": str(tmp_path), "environment": "local",
        "runtime": {"type": runtime, "base_url": "http://127.0.0.1:8000", "compose_file": "compose.yml"},
        "services": {"app": {"type": service_type, "root": ".", "dockerfile": "Dockerfile", **service}},
    })


def test_manager_selects_compose_adapter(tmp_path: Path) -> None:
    profile = _profile(tmp_path, "docker_compose", "django", healthcheck={"path": "/health/"})
    assert isinstance(SandboxManager().select_adapter(profile), DockerComposeAdapter)


def test_manager_selects_dockerfile_adapter(tmp_path: Path) -> None:
    profile = _profile(tmp_path, "dockerfile", "unknown")
    assert isinstance(SandboxManager().select_adapter(profile), DockerfileAdapter)


@pytest.mark.parametrize("service_type", ["django", "node+vite"])
def test_manager_selects_framework_adapter(tmp_path: Path, service_type: str) -> None:
    profile = _profile(tmp_path, "unknown", service_type, build=["echo", "build"], run=["echo", "run"])
    assert isinstance(SandboxManager().select_adapter(profile), FrameworkAdapter)


def test_manager_fails_closed_for_unknown_runtime(tmp_path: Path) -> None:
    profile = _profile(tmp_path, "unknown", "flask")
    with pytest.raises(SandboxConfigurationError, match="Unsupported"):
        SandboxManager().select_adapter(profile)


def test_manager_rejects_production_environment(tmp_path: Path) -> None:
    profile = _profile(tmp_path, "dockerfile", "unknown").model_copy(
        update={"environment": "production"}
    )
    with pytest.raises(SandboxConfigurationError, match="not allowed"):
        SandboxManager().select_adapter(profile)


def test_dockerfile_adapter_rejects_ambiguous_services(tmp_path: Path) -> None:
    profile = _profile(tmp_path, "dockerfile", "unknown")
    duplicate = profile.services["app"].model_copy(update={"id": "other"})
    profile = profile.model_copy(update={"services": {"app": profile.services["app"], "other": duplicate}})
    with pytest.raises(SandboxConfigurationError, match="exactly one Dockerfile"):
        SandboxManager().open(profile)


def test_dockerfile_lifecycle_uses_fixed_argv_logs_and_teardown(tmp_path: Path) -> None:
    profile = _profile(tmp_path, "dockerfile", "unknown")
    runner = FakeRunner()
    session = SandboxManager(runner=runner).open(profile)
    with session:
        assert session.ready is True
        assert {log.stage for log in session.logs} >= {"build", "run"}
        assert "secret-value" not in session.logs[0].stdout
    commands = [" ".join(command) for command in runner.commands]
    assert any(command.startswith("docker build --label cft.session_id=") for command in commands)
    assert any(command.startswith("docker run --detach --name cft-target-") for command in commands)
    assert any(command.startswith("docker rm --force cft-target-") for command in commands)
    assert all(isinstance(command, list) for command in runner.commands)


def test_dockerfile_path_containment_is_enforced(tmp_path: Path) -> None:
    profile = _profile(tmp_path, "dockerfile", "unknown")
    service = profile.services["app"].model_copy(update={"dockerfile": "../Dockerfile"})
    # Модель обычно блокирует traversal; model_copy simulates a corrupted external object.
    profile = profile.model_copy(update={"services": {"app": service}})
    with pytest.raises(SandboxConfigurationError, match="inside the repository"):
        SandboxManager().open(profile)


def test_timeout_records_structured_log_and_tears_down(tmp_path: Path) -> None:
    profile = _profile(tmp_path, "dockerfile", "unknown")
    runner = FakeRunner(timeout_on="build")
    session = SandboxManager(runner=runner).open(profile)
    with pytest.raises(TimeoutError):
        session.start()
    assert session.status == "timed_out"
    assert any(log.timed_out for log in session.logs)
    assert any(command[:3] == ["docker", "rm", "--force"] for command in runner.commands)
