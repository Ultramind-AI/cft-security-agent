from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from executor import worker
from executor.sandbox import (
    DockerSandbox,
    ProcessSandbox,
    SandboxRequest,
    get_minimal_environment,
)
from executor.sandbox_manager import SandboxConfigurationError, SandboxManager
from executor.sandbox_policy import SandboxPolicy
from executor.sandbox_runtime import DockerRuntimeBuilder
from schemas.target import TargetProfile


def test_registered_targets_keep_security_boundary_disabled(
    registered_target_profile: TargetProfile,
) -> None:
    constraints = registered_target_profile.constraints

    assert constraints.host_filesystem is False
    assert constraints.ci_secrets is False
    assert constraints.docker_socket is False
    assert constraints.external_network is False


def test_registered_targets_reject_external_runtime_urls(
    registered_target_profile: TargetProfile,
) -> None:
    with pytest.raises(ValueError, match="fixed absolute URL path"):
        registered_target_profile.build_url("https://example.test/health/")


def test_minimal_environment_removes_ci_and_host_secrets() -> None:
    clean = get_minimal_environment(
        {
            "PATH": "/usr/bin",
            "GITHUB_TOKEN": "must-not-leak",
            "CI_JOB_TOKEN": "must-not-leak",
            "AWS_SECRET_ACCESS_KEY": "must-not-leak",
            "CFT_API_KEY": "must-not-leak",
            "PASSWORD": "must-not-leak",
        }
    )

    assert clean["PATH"] == "/usr/bin"
    assert not {"GITHUB_TOKEN", "CI_JOB_TOKEN", "AWS_SECRET_ACCESS_KEY", "CFT_API_KEY", "PASSWORD"} & set(clean)


def test_docker_runtime_has_only_read_only_trusted_mounts_and_no_socket(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    spec = DockerRuntimeBuilder(
        SandboxPolicy(backend="docker", network_mode="none", sandbox_image="example@sha256:test")
    ).build_spec(
        run_id="boundary",
        target_repo_host_path=repo,
        worker_script_host_path=Path(worker.__file__).resolve(),
    )
    mounts = [spec.argv[index + 1] for index, value in enumerate(spec.argv) if value == "-v"]

    assert len(mounts) == 2
    assert [mount for mount in mounts if mount.endswith(":/target:ro")] == [
        f"{repo.resolve()}:/target:ro"
    ]
    assert all(mount.endswith(":ro") for mount in mounts)
    assert not any("docker.sock" in mount for mount in mounts)
    assert "--privileged" not in spec.argv


def test_generic_docker_command_cannot_enable_external_network(tmp_path: Path) -> None:
    spec = DockerRuntimeBuilder(
        SandboxPolicy(backend="docker", network_mode="none", sandbox_image="example@sha256:test")
    ).build_spec(
        run_id="network",
        target_repo_host_path=None,
        worker_script_host_path=Path(worker.__file__).resolve(),
        target_network_enabled=True,
    )

    assert spec.argv[spec.argv.index("--network") + 1] == "none"
    with pytest.raises(ValueError, match="trusted target network"):
        DockerSandbox(
            SandboxPolicy(backend="docker", network_mode="none", sandbox_image="example@sha256:test")
        ).open_sequence(network_name="host")


def test_failed_process_run_removes_disposable_workspace(tmp_path: Path) -> None:
    worker_script = tmp_path / "failing_worker.py"
    worker_script.write_text("raise SystemExit(7)\n", encoding="utf-8")
    workspace = tmp_path / "workspaces"
    result = ProcessSandbox(workspace_root=workspace, worker_path=worker_script).run(
        SandboxRequest(
            run_id="failed-cleanup",
            tool="safe_noop",
            base_url="",
            parameters={},
            request_timeout_seconds=1,
        )
    )

    assert result.exit_code == 7
    assert not list(workspace.iterdir())


def test_build_failure_triggers_target_cleanup(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    profile = TargetProfile.model_validate(
        {
            "id": "build-failure-target",
            "repository_path": str(tmp_path),
            "runtime": {"type": "dockerfile"},
            "services": {"app": {"type": "unknown", "root": ".", "dockerfile": "Dockerfile"}},
        }
    )
    commands: list[list[str]] = []

    def runner(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        if argv[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(argv, 1, "", "build failed")
        return subprocess.CompletedProcess(argv, 0, "", "")

    session = SandboxManager(runner=runner).open(profile)
    with pytest.raises(SandboxConfigurationError, match="build failed"):
        session.start()

    assert session.status == "failed"
    assert any(command[:3] == ["docker", "rm", "--force"] for command in commands)
    assert any(command[:3] == ["docker", "image", "rm"] for command in commands)
