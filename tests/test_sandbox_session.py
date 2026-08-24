from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from executor.sandbox_session import (
    SandboxSession,
    SessionCleanupError,
    SessionStatus,
    SessionTimeoutError,
    normalize_compose_ps,
)
from schemas.target import TargetProfile


class FakeCompose:
    def __init__(
        self,
        *,
        fail_up: bool = False,
        timeout_up: bool = False,
        fail_resource_query: bool = False,
    ) -> None:
        self.commands: list[list[str]] = []
        self.timeouts: list[float] = []
        self.fail_up = fail_up
        self.timeout_up = timeout_up
        self.fail_resource_query = fail_resource_query

    def __call__(self, argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        self.timeouts.append(timeout)
        action = argv[-1]
        if action == "config":
            return subprocess.CompletedProcess(argv, 0, "services:\n  app: {}\n", "")
        if "up" in argv:
            if self.timeout_up:
                raise subprocess.TimeoutExpired(argv, timeout)
            return subprocess.CompletedProcess(argv, 1 if self.fail_up else 0, "", "up failed" if self.fail_up else "")
        if action == "json":
            return subprocess.CompletedProcess(argv, 0, '[{"Service":"app","State":"running"}]', "")
        is_resource_query = (
            argv[:2] == ["docker", "ps"]
            or argv[:3] == ["docker", "network", "ls"]
            or argv[:3] == ["docker", "volume", "ls"]
        )
        if is_resource_query and self.fail_resource_query:
            return subprocess.CompletedProcess(argv, 1, "", "query failed")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _target(tmp_path: Path) -> TargetProfile:
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    return TargetProfile.model_validate(
        {
            "id": "test",
            "environment": "local",
            "repository_path": str(tmp_path),
            "runtime": {
                "base_url": "http://127.0.0.1:8000",
                "compose_file": compose.name,
            },
            "services": {
                "app": {
                    "type": "compose",
                    "root": ".",
                    "healthcheck": {"path": "/health/"},
                }
            },
        }
    )


def test_session_ids_and_compose_projects_are_unique(tmp_path: Path) -> None:
    one = SandboxSession(
        _target(tmp_path),
        runner=FakeCompose(),
        health_probe=lambda url, timeout: True,
    )
    two = SandboxSession(
        _target(tmp_path),
        runner=FakeCompose(),
        health_probe=lambda url, timeout: True,
    )
    assert one.session_id != two.session_id
    assert one.compose_project != two.compose_project
    one.teardown()
    two.teardown()


def test_lifecycle_order_state_and_idempotent_teardown(tmp_path: Path) -> None:
    fake = FakeCompose()
    session = SandboxSession(_target(tmp_path), runner=fake, health_probe=lambda url, timeout: True)
    with session:
        state = session.collect_state()
        assert state["ready"] is True
        assert state["services"][0]["Service"] == "app"
    session.teardown()
    assert session.status == SessionStatus.CLOSED
    names = [" ".join(command) for command in fake.commands]
    assert names.index(next(item for item in names if item.endswith(" config"))) < names.index(next(item for item in names if " up --build --detach" in item))
    assert any(" down --volumes --remove-orphans" in item for item in names)
    assert all(isinstance(command, list) for command in fake.commands)


def test_compose_session_resets_all_host_ports_with_server_owned_override(
    tmp_path: Path,
) -> None:
    fake = FakeCompose()
    session = SandboxSession(
        _target(tmp_path),
        runner=fake,
        health_probe=lambda url, timeout: True,
    )

    session.prepare()

    override = session.info.working_directory / "compose.sandbox.override.yml"
    assert override.read_text(encoding="utf-8") == (
        'services:\n  "app":\n    ports: !reset []\n'
    )
    config_commands = [command for command in fake.commands if command[-1] == "config"]
    assert len(config_commands) == 2
    assert str(override) not in config_commands[0]
    assert str(override) in config_commands[1]
    session.teardown()


def test_normalize_compose_ps_supports_array_object_and_json_lines() -> None:
    record = {
        "Service": "backend",
        "Name": "project-backend-1",
        "State": "running",
        "Health": "healthy",
        "Status": "Up 10 seconds (healthy)",
        "ID": "synthetic-id",
        "Command": "python -c \"print('quoted')\"",
    }
    encoded = json.dumps(record)

    for stdout in (json.dumps([record]), encoded, f"{encoded}\n{encoded}"):
        services = normalize_compose_ps(stdout)
        assert len(services) == (2 if "\n" in stdout else 1)
        assert services[0] == {
            "Service": "backend", "Name": "project-backend-1", "State": "running",
            "Health": "healthy", "Status": "Up 10 seconds (healthy)", "ID": "synthetic-id",
        }


def test_normalize_compose_ps_keeps_valid_json_lines_when_one_line_is_broken() -> None:
    valid = '{"service":"backend","state":"running","health":"healthy"}'
    services = normalize_compose_ps(f"{valid}\n{{not json}}\n{valid}")

    assert services[0] == {"Service": "backend", "State": "running", "Health": "healthy"}
    assert "raw" in services[1]
    assert services[2] == services[0]


def test_normalize_compose_ps_returns_empty_list_for_empty_output() -> None:
    assert normalize_compose_ps("") == []


def test_start_failure_always_tears_down(tmp_path: Path) -> None:
    fake = FakeCompose(fail_up=True)
    session = SandboxSession(_target(tmp_path), runner=fake, health_probe=lambda url, timeout: True)
    with pytest.raises(Exception, match="up failed"):
        session.start()
    assert session.status == SessionStatus.CLOSED
    assert any(" down --volumes --remove-orphans" in " ".join(command) for command in fake.commands)


def test_compose_up_uses_dedicated_startup_timeout_and_tears_down(tmp_path: Path) -> None:
    fake = FakeCompose(timeout_up=True)
    session = SandboxSession(
        _target(tmp_path),
        command_timeout=7,
        startup_timeout=123,
        runner=fake,
        health_probe=lambda url, timeout: True,
    )
    with pytest.raises(SessionTimeoutError):
        session.start()
    up_index = next(index for index, command in enumerate(fake.commands) if "up" in command)
    assert fake.timeouts[up_index] == 123
    assert fake.timeouts[0] == 7  # compose config remains a short command.
    down = next(command for command in fake.commands if "down" in command)
    assert down[down.index("--project-name") + 1] == session.compose_project
    assert session.status == SessionStatus.CLOSED


def test_context_exception_always_tears_down(tmp_path: Path) -> None:
    fake = FakeCompose()
    session = SandboxSession(_target(tmp_path), runner=fake, health_probe=lambda url, timeout: True)
    with pytest.raises(RuntimeError, match="controlled"), session:
        raise RuntimeError("controlled")
    assert session.status == SessionStatus.CLOSED


def test_readiness_timeout_always_tears_down(tmp_path: Path) -> None:
    fake = FakeCompose()
    ticks = iter((0.0, 0.0, 2.0))
    session = SandboxSession(_target(tmp_path), runner=fake, health_probe=lambda url, timeout: False, readiness_timeout=1, clock=lambda: next(ticks), sleep=lambda _: None)
    with pytest.raises(SessionTimeoutError):
        session.start()
    assert session.status == SessionStatus.CLOSED
    assert any(" down --volumes --remove-orphans" in " ".join(command) for command in fake.commands)


def test_container_cleanup_uses_valid_docker_ps_command(tmp_path: Path) -> None:
    fake = FakeCompose()
    session = SandboxSession(_target(tmp_path), runner=fake)
    session.teardown()
    container_query = next(
        command for command in fake.commands if command[:2] == ["docker", "ps"]
    )
    assert "ls" not in container_query
    assert "--all" in container_query
    assert "--quiet" in container_query
    assert f"label=com.docker.compose.project={session.compose_project}" in container_query


def test_cleanup_query_failure_is_not_treated_as_empty_result(tmp_path: Path) -> None:
    session = SandboxSession(
        _target(tmp_path),
        runner=FakeCompose(fail_resource_query=True),
    )
    with pytest.raises(SessionCleanupError, match="could not be confirmed"):
        session.teardown()
    assert session.status == SessionStatus.FAILED


def test_compose_file_must_stay_inside_target_repository(tmp_path: Path) -> None:
    repository = tmp_path / "target"
    repository.mkdir()
    outside = tmp_path / "compose.yml"
    outside.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must stay inside"):
        TargetProfile.model_validate(
            {
                "id": "test",
                "environment": "local",
                "repository_path": str(repository),
                "runtime": {
                    "base_url": "http://127.0.0.1:8000",
                    "compose_file": "../compose.yml",
                },
                "services": {
                    "app": {
                        "type": "compose",
                        "root": ".",
                        "healthcheck": {"path": "/health/"},
                    }
                },
            }
        )
