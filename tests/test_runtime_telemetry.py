from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from evidence.telemetry import JsonRuntimeTelemetryStore, build_telemetry_evidence
from executor.runtime_telemetry import RuntimeTelemetryCollector
from executor.sandbox_manager import SandboxLog
from schemas.action import ActionProposal


class _Session:
    def __init__(self, repository: Path) -> None:
        self.session_id = "session-telemetry"
        self.target = SimpleNamespace(
            id="target-local",
            repository_path=repository,
        )
        self.logs = [
            SandboxLog(
                session_id=self.session_id,
                adapter="docker_compose",
                stage="run",
                service="backend",
                argv=("docker", "compose", "up"),
                status="ok",
                stdout="backend started",
                stderr="",
                exit_code=0,
                timed_out=False,
                duration_ms=120,
                captured_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]

    def collect_state(self) -> dict[str, object]:
        return {"compose_project": "cft-sandbox-test", "ready": True}


class _DockerRunner:
    def __init__(self, *, trusted_label: bool = True) -> None:
        self.commands: list[list[str]] = []
        self.trusted_label = trusted_label

    def __call__(
        self, argv: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(argv, 0, "abc123\n", "")
        if argv[:3] == ["docker", "inspect", "--type"]:
            project = "cft-sandbox-test" if self.trusted_label else "another-project"
            payload = [
                {
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": project,
                            "com.docker.compose.service": "backend",
                        }
                    },
                    "State": {
                        "Status": "running",
                        "Running": True,
                        "ExitCode": 0,
                        "StartedAt": "2026-01-01T00:00:01Z",
                        "FinishedAt": "",
                        "Health": {"Status": "healthy"},
                    },
                }
            ]
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        if argv[:3] == ["docker", "logs", "--timestamps"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                "2026-01-01T00:00:02Z listening token=secret-value\n",
                "2026-01-01T00:00:03Z database unavailable\n",
            )
        if argv[:3] == ["docker", "exec", "abc123"]:
            if argv[-1] == "/proc/net/tcp":
                proc_net = (
                    "sl local_address rem_address st\n"
                    "0: 00000000:1F40 00000000:0000 0A\n"
                )
                return subprocess.CompletedProcess(argv, 0, proc_net, "")
            return subprocess.CompletedProcess(argv, 1, "", "not available")
        if argv[:2] == ["docker", "events"]:
            payload = {
                "Type": "container",
                "Action": "die",
                "id": "abc123456789",
                "time": 1767225604,
                "Actor": {
                    "Attributes": {
                        "com.docker.compose.project": "cft-sandbox-test",
                        "com.docker.compose.service": "backend",
                        "exitCode": "1",
                    }
                },
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        raise AssertionError(f"Unexpected Docker command: {argv}")


def test_collector_builds_session_scoped_timeline(tmp_path: Path) -> None:
    runner = _DockerRunner()
    timeline = RuntimeTelemetryCollector(
        _Session(tmp_path), runner=runner
    ).collect(run_id="run-1")

    assert timeline.session_id == "session-telemetry"
    assert timeline.run_id == "run-1"
    assert [event.sequence for event in timeline.events] == list(
        range(len(timeline.events))
    )
    assert {event.kind for event in timeline.events} >= {
        "start_log",
        "runtime_log",
        "runtime_error",
        "container_state",
        "listening_port",
        "process_exit",
    }
    assert all(event.session_id == timeline.session_id for event in timeline.events)
    assert all(event.run_id == timeline.run_id for event in timeline.events)

    port = next(event for event in timeline.events if event.kind == "listening_port")
    assert port.facts == {"family": "ipv4", "port": 8000, "protocol": "tcp"}
    error = next(event for event in timeline.events if event.kind == "runtime_error")
    assert error.facts["stream"] == "stderr"
    assert any(
        "<redacted>" in str(event.facts.get("message", ""))
        for event in timeline.events
    )

    commands = [" ".join(command) for command in runner.commands]
    assert all("docker info" not in command for command in commands)
    assert all("HostConfig" not in command and "docker top" not in command for command in commands)
    assert any(
        "label=com.docker.compose.project=cft-sandbox-test" in command
        for command in commands
    )


def test_collector_rechecks_container_label(tmp_path: Path) -> None:
    timeline = RuntimeTelemetryCollector(
        _Session(tmp_path), runner=_DockerRunner(trusted_label=False)
    ).collect()

    assert not any(event.source == "docker_logs" for event in timeline.events)
    assert any(
        event.facts.get("operation") == "reject_unscoped_container"
        for event in timeline.events
    )


def test_timeline_store_and_evidence_link_preserve_event_identity(tmp_path: Path) -> None:
    timeline = RuntimeTelemetryCollector(
        _Session(tmp_path), runner=_DockerRunner()
    ).collect(run_id="run-1")
    store = JsonRuntimeTelemetryStore(tmp_path / "telemetry")
    artifact_ref, path = store.put(timeline)

    assert Path(path).is_file()
    restored = store.get(artifact_ref)
    event = next(item for item in restored.events if item.kind == "process_exit")
    action = ActionProposal(
        id="action-1",
        tool="observe_http_surface",
        target="target-local",
        environment="sandbox",
        service="backend",
        purpose="Explain a target failure.",
        expected_evidence="A session-scoped target event.",
    )
    evidence = build_telemetry_evidence(
        event=event,
        action=action,
        hypothesis_id="hypothesis-1",
        artifact_ref=artifact_ref,
    )

    assert evidence.source == "runtime"
    assert evidence.sandbox_session_id == timeline.session_id
    assert evidence.action.run_id == timeline.run_id
    assert evidence.observation.facts["telemetry_event_id"] == event.id
    assert evidence.artifacts[0].role == "log"

    with pytest.raises(ValueError, match="Invalid telemetry"):
        store.get("../outside")
