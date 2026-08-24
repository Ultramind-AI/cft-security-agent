from __future__ import annotations

import io

from executor.approvals import InMemoryApprovalStore
from executor.sandbox import DockerSequenceRuntime, SandboxRequest
from executor.sandbox_policy import SandboxPolicy
from executor.sandbox_runner import SandboxRunner
from schemas.action import ActionProposal
from schemas.execution import ExecutionResult
from schemas.runtime import RuntimeService, RuntimeServiceMap
from schemas.validation import ValidationResult
from tools.registry import ToolRegistry


def _action(action_id: str, **updates: object) -> ActionProposal:
    return ActionProposal.model_validate({
        "id": action_id,
        "tool": "safe_noop",
        "target": "target",
        "purpose": "test",
        "expected_evidence": "test",
        "service": "api",
        "endpoint": "/health/",
        **updates,
    })


def _approved(*actions: ActionProposal) -> InMemoryApprovalStore:
    store = InMemoryApprovalStore()
    for action in actions:
        store.record(action, ValidationResult(approved=True, action_id=action.id, reason="test"))
    return store


def _runtime_map() -> RuntimeServiceMap:
    return RuntimeServiceMap(
        session_id="session-1",
        services={"api": RuntimeService(name="api", type="django", address="http://api:8000", ready=True, readiness_source="http_probe", allowed_endpoints=["/health/"])},
    )


def _runner(actions: list[ActionProposal], *, max_actions: int = 8, timeout: float = 30):
    registry = ToolRegistry()
    registry.register("safe_noop", object())
    seen: list[tuple[str, str]] = []

    def execute(action: ActionProposal, run_id: str, session_id: str | None) -> ExecutionResult:
        seen.append((action.id, run_id))
        return ExecutionResult(run_id=run_id, action_id=action.id, status="completed", exit_code=0, stdout="x" * 20_000, evidence_ref=f"evidence-{action.id}", audit_ref=f"audit:{run_id}")

    return SandboxRunner(approvals=_approved(*actions), registry=registry, execute_one=execute, max_actions=max_actions, sequence_timeout=timeout), seen


def test_runner_executes_approved_sequence_with_one_run_id() -> None:
    actions = [_action("one"), _action("two")]
    runner, seen = _runner(actions)
    result = runner.run(actions, runtime_services=_runtime_map())
    assert result.status == "completed"
    assert [item.action_id for item in result.results] == ["one", "two"]
    assert {item.run_id for item in result.results} == {result.run_id}
    assert all(item.session_id == "session-1" for item in result.results)
    assert len(result.results[0].stdout) <= 16_384
    assert seen == [("one", result.run_id), ("two", result.run_id)]


def test_runner_blocks_unapproved_or_unknown_action_before_execution() -> None:
    action = _action("one")
    registry = ToolRegistry()
    registry.register("safe_noop", object())
    runner = SandboxRunner(approvals=InMemoryApprovalStore(), registry=registry, execute_one=lambda *_: None)
    result = runner.run([action], runtime_services=_runtime_map())
    assert result.status == "denied"
    assert "No trusted approval" in result.results[0].stderr


def test_runner_checks_service_and_endpoint_scope() -> None:
    action = _action("outside", service="outside")
    runner, seen = _runner([action])
    result = runner.run([action], runtime_services=_runtime_map())
    assert result.status == "denied"
    assert "not ready" in result.results[0].stderr
    assert seen == []


def test_runner_refuses_http_observation_without_a_runtime_service_map() -> None:
    action = _action("runtime-map-required", tool="observe_http_surface")
    registry = ToolRegistry()
    registry.register("observe_http_surface", object())
    called = False

    def execute(*_args) -> ExecutionResult:
        nonlocal called
        called = True
        raise AssertionError("runtime observation must be denied before execution")

    result = SandboxRunner(
        approvals=_approved(action), registry=registry, execute_one=execute
    ).run([action])

    assert result.status == "denied"
    assert "RuntimeServiceMap" in result.results[0].stderr
    assert called is False


def test_runner_rejects_endpoint_substitution_against_capability_contract() -> None:
    action = _action("substitution", tool="observe_http_surface", endpoint="/health/")
    registry = ToolRegistry()
    registry.register("observe_http_surface", object(), endpoint="/api/projects/")
    called = False

    def execute(action: ActionProposal, run_id: str, session_id: str | None) -> ExecutionResult:
        nonlocal called
        called = True
        return ExecutionResult(run_id=run_id, action_id=action.id, status="completed", exit_code=0, evidence_ref="e", audit_ref="a")

    result = SandboxRunner(approvals=_approved(action), registry=registry, execute_one=execute).run([action], runtime_services=_runtime_map())
    assert result.status == "denied"
    assert "does not match" in result.results[0].stderr
    assert called is False

    missing_endpoint = _action("missing-endpoint", tool="observe_http_surface", endpoint=None)
    result = SandboxRunner(approvals=_approved(missing_endpoint), registry=registry, execute_one=execute).run([missing_endpoint], runtime_services=_runtime_map())
    assert result.status == "denied"
    assert "approved service endpoint" in result.results[0].stderr

    action = _action("endpoint", endpoint="/admin/")
    runner, seen = _runner([action])
    result = runner.run([action], runtime_services=_runtime_map())
    assert "not allowed" in result.results[0].stderr
    assert seen == []


def test_runner_blocks_raw_shell_parameters_via_registry_validation_boundary() -> None:
    action = _action("raw", tool="raw_shell", parameters={"command": "whoami"})
    runner, seen = _runner([action])
    result = runner.run([action], runtime_services=_runtime_map())
    assert result.status == "denied"
    assert "Unknown executor tool" in result.results[0].stderr
    assert seen == []


def test_runner_limits_actions_and_stops_after_error() -> None:
    actions = [_action("one"), _action("two")]
    runner, seen = _runner(actions, max_actions=1)
    assert runner.run(actions, runtime_services=_runtime_map()).status == "denied"
    assert seen == []

    registry = ToolRegistry()
    registry.register("safe_noop", object())
    calls: list[str] = []

    def fails(action: ActionProposal, run_id: str, session_id: str | None) -> ExecutionResult:
        calls.append(action.id)
        return ExecutionResult(run_id=run_id, action_id=action.id, status="failed", exit_code=1, evidence_ref="e", audit_ref="a")

    runner = SandboxRunner(approvals=_approved(*actions), registry=registry, execute_one=fails)
    assert runner.run(actions, runtime_services=_runtime_map()).status == "failed"
    assert calls == ["one"]


def test_runner_sequence_timeout_is_structured(monkeypatch) -> None:
    actions = [_action("slow"), _action("after-slow")]
    registry = ToolRegistry()
    registry.register("safe_noop", object())

    def slow(action: ActionProposal, run_id: str, session_id: str | None) -> ExecutionResult:
        return ExecutionResult(run_id=run_id, action_id=action.id, status="completed", exit_code=0, evidence_ref="e", audit_ref="a")

    ticks = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr("executor.sandbox_runner.monotonic", lambda: next(ticks))
    runner = SandboxRunner(approvals=_approved(*actions), registry=registry, execute_one=slow, sequence_timeout=0.001)
    result = runner.run(actions, runtime_services=_runtime_map())
    assert result.results[-1].timed_out is True


def test_persistent_docker_runtime_uses_one_container_and_execs(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    class Completed:
        def __init__(self, stdout="", stderr="", returncode=0):
            self.stdout, self.stderr, self.returncode = stdout, stderr, returncode

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return Completed("container-id\n" if "run" in argv else "ok")

    monkeypatch.setattr("executor.sandbox.subprocess.run", fake_run)
    class Proc:
        def __init__(self): self.stdin, self.stdout, self.stderr, self.returncode = io.BytesIO(), io.BytesIO(b"ok"), io.BytesIO(), 0
        def poll(self): return 0
        def kill(self): pass
    def fake_popen(argv, **kwargs): calls.append(argv); return Proc()
    monkeypatch.setattr("executor.sandbox.subprocess.Popen", fake_popen)
    worker = tmp_path / "worker.py"
    worker.write_text("", encoding="utf-8")
    runtime = DockerSequenceRuntime(SandboxPolicy(backend="docker", sandbox_image="registry/example@sha256:" + "a" * 64), worker, "cft-sandbox-test_default", None)
    runtime.start()
    request = SandboxRequest(run_id="one", tool="safe_noop", base_url="http://api", parameters={}, request_timeout_seconds=1)
    runtime.run(request)
    runtime.run(request)
    runtime.close()
    runtime.close()
    assert sum(command[:2] == ["docker", "run"] for command in calls) == 1
    assert sum(command[:2] == ["docker", "exec"] for command in calls) == 2
    assert sum(command[:3] == ["docker", "rm", "--force"] for command in calls) == 1
    assert runtime.runtime_instance_id == "container-id"


def test_persistent_docker_runtime_decodes_utf8_and_handles_empty_output(monkeypatch, tmp_path) -> None:
    class Completed:
        def __init__(self, stdout=None, stderr=None, returncode=0):
            self.stdout, self.stderr, self.returncode = stdout, stderr, returncode

    def fake_run(argv, **kwargs):
        if argv[:2] == ["docker", "run"]:
            return Completed("container-id\n")
        return Completed()

    monkeypatch.setattr("executor.sandbox.subprocess.run", fake_run)
    class Proc:
        def __init__(self): self.stdin, self.stdout, self.stderr, self.returncode = io.BytesIO(), io.BytesIO(), io.BytesIO(), 0
        def poll(self): return 0
        def kill(self): pass
    monkeypatch.setattr("executor.sandbox.subprocess.Popen", lambda *args, **kwargs: Proc())
    worker = tmp_path / "worker.py"
    worker.write_text("", encoding="utf-8")
    runtime = DockerSequenceRuntime(SandboxPolicy(backend="docker", sandbox_image="registry/example@sha256:" + "a" * 64), worker, "cft-sandbox-test_default", None).start()
    result = runtime.run(SandboxRequest(run_id="one", tool="safe_noop", base_url="http://api", parameters={}, request_timeout_seconds=1))
    runtime.close()
    assert result.stdout == ""
    assert result.stderr == ""


def test_runner_rejects_sandbox_command_runtime_scope() -> None:
    action = _action(
        "sandbox-network-scope",
        tool="sandbox_command",
        parameters={"argv": ["python", "-V"], "cwd": "/target"},
        service="api",
        endpoint="/health/",
    )
    registry = ToolRegistry()
    registry.register("sandbox_command", object())
    called = False

    def execute(*_args) -> ExecutionResult:
        nonlocal called
        called = True
        raise AssertionError("sandbox command must be denied before execution")

    result = SandboxRunner(
        approvals=_approved(action),
        registry=registry,
        execute_one=execute,
    ).run([action], runtime_services=_runtime_map())

    assert result.status == "denied"
    assert "cannot request runtime network scope" in result.results[0].stderr
    assert called is False
