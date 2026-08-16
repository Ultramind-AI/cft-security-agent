import json
import os
import sys

import pytest

from executor import worker
from executor.sandbox import (
    ProcessSandbox,
    RunLimiter,
    SandboxLimits,
    SandboxRequest,
)


def _limits(*, wall_time_seconds: float = 1.0) -> SandboxLimits:
    return SandboxLimits(
        wall_time_seconds=wall_time_seconds,
        cpu_time_seconds=1,
        memory_bytes=128 * 1024 * 1024,
        max_file_bytes=4096,
        max_processes=4,
        max_output_bytes=1024,
    )


def _request(run_id: str = "sandbox-test") -> SandboxRequest:
    return SandboxRequest(
        run_id=run_id,
        tool="safe_noop",
        base_url="http://127.0.0.1:8000",
        parameters={"message": "hello", "test_outcome": "confirmed"},
        request_timeout_seconds=0.5,
    )


def test_process_sandbox_runs_fixed_worker_in_disposable_directory(tmp_path) -> None:
    workspace_root = tmp_path / "workspaces"
    sandbox = ProcessSandbox(
        workspace_root=workspace_root,
        limits=_limits(),
    )

    result = sandbox.run(_request())

    assert result.exit_code == 0
    assert result.stdout == "safe_noop:hello:outcome=confirmed"
    assert result.stderr == ""
    assert result.timed_out is False
    assert list(workspace_root.iterdir()) == []


def test_process_sandbox_timeout_is_a_result_not_an_exception(tmp_path) -> None:
    worker_path = tmp_path / "hung_worker.py"
    worker_path.write_text(
        "import time\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    sandbox = ProcessSandbox(
        workspace_root=tmp_path / "workspaces",
        limits=_limits(wall_time_seconds=0.1),
        worker_path=worker_path,
    )

    result = sandbox.run(_request("hung-run"))

    assert result.exit_code == 124
    assert result.timed_out is True
    assert "timed out" in result.stderr
    assert result.duration_ms < 3000


def test_process_sandbox_captures_stderr_and_nonzero_exit(tmp_path) -> None:
    worker_path = tmp_path / "error_worker.py"
    worker_path.write_text(
        "import sys\nprint('controlled failure', file=sys.stderr)\nraise SystemExit(7)\n",
        encoding="utf-8",
    )
    sandbox = ProcessSandbox(
        workspace_root=tmp_path / "workspaces",
        limits=_limits(),
        worker_path=worker_path,
    )

    result = sandbox.run(_request("error-run"))

    assert result.exit_code == 7
    assert result.stdout == ""
    assert result.stderr.strip() == "controlled failure"
    assert result.timed_out is False


def test_run_limiter_blocks_parallel_start_without_consuming_retry() -> None:
    limiter = RunLimiter(max_runs_per_action=1, max_concurrent_runs=1)

    assert limiter.acquire("first")[0] is True
    blocked, reason = limiter.acquire("second")
    assert blocked is False
    assert "Concurrent run limit" in reason

    limiter.release()
    assert limiter.acquire("second")[0] is True
    limiter.release()


@pytest.mark.skipif(os.name != "posix", reason="POSIX resource limits")
def test_process_sandbox_applies_os_resource_limits(tmp_path) -> None:
    worker_path = tmp_path / "limits_worker.py"
    worker_path.write_text(
        "import json, resource\n"
        "print(json.dumps({"
        "'cpu': resource.getrlimit(resource.RLIMIT_CPU)[0],"
        "'memory': resource.getrlimit(resource.RLIMIT_AS)[0],"
        "'file': resource.getrlimit(resource.RLIMIT_FSIZE)[0],"
        "'processes': resource.getrlimit(resource.RLIMIT_NPROC)[0]"
        "}))\n",
        encoding="utf-8",
    )
    limits = _limits()
    sandbox = ProcessSandbox(
        workspace_root=tmp_path / "workspaces",
        limits=limits,
        worker_path=worker_path,
        python_executable=sys.executable,
    )

    result = sandbox.run(_request("limits-run"))
    applied = json.loads(result.stdout)

    assert result.exit_code == 0
    assert applied["cpu"] == limits.cpu_time_seconds
    assert applied["file"] == limits.max_file_bytes
    assert applied["processes"] == limits.max_processes
    if sys.platform == "darwin":
        # macOS exposes RLIMIT_AS but rejects the configured address-space cap
        # during subprocess pre-exec, so memory is intentionally left unchanged.
        assert applied["memory"] != limits.memory_bytes
    else:
        assert applied["memory"] == limits.memory_bytes


def test_worker_maps_health_tool_to_fixed_path(monkeypatch) -> None:
    requested: list[tuple[str, float, int]] = []

    def fake_get(url: str, timeout: float, output_limit: int):
        requested.append((url, timeout, output_limit))
        return 0, '{"status":"ok","database":"ok"}', ""

    monkeypatch.setattr(worker, "_http_get", fake_get)

    result = worker._execute(
        {
            "tool": "check_sberlab_health",
            "base_url": "http://127.0.0.1:8000",
            "parameters": {},
            "request_timeout_seconds": 1.5,
            "max_output_bytes": 2048,
        }
    )

    assert result[0] == 0
    assert requested == [("http://127.0.0.1:8000/health/", 1.5, 2048)]


def test_worker_never_uses_parameter_as_url(monkeypatch) -> None:
    called = False

    def fake_get(url: str, timeout: float, output_limit: int):
        nonlocal called
        called = True
        return 0, "{}", ""

    monkeypatch.setattr(worker, "_http_get", fake_get)

    result = worker._execute(
        {
            "tool": "get_sberlab_public_projects",
            "base_url": "http://127.0.0.1:8000",
            "parameters": {"url": "http://example.com"},
            "request_timeout_seconds": 1,
            "max_output_bytes": 1024,
        }
    )

    assert result[0] == 2
    assert called is False


def test_worker_dockerfile_check_never_accepts_agent_path_parameter(tmp_path) -> None:
    dockerfile = tmp_path / "backend" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")

    result = worker._execute(
        {
            "tool": "inspect_dockerfile_user",
            "repository_path": str(tmp_path),
            "artifacts": {
                "backend_dockerfile": {
                    "kind": "dockerfile",
                    "path": "backend/Dockerfile",
                }
            },
            "parameters": {
                "artifact_id": "backend_dockerfile",
                "path": "../../outside",
            },
            "request_timeout_seconds": 1,
            "max_output_bytes": 1024,
        }
    )

    assert result[0] == 1
    assert "Unsupported capability parameters" in result[2]
