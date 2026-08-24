import json
import os
import sys
from pathlib import Path

import pytest

from executor import worker
from executor.executor import CAPABILITY_NETWORK_ACCESS
from executor.sandbox import (
    STRICT_ALLOWED_ENV,
    ProcessSandbox,
    RunLimiter,
    SandboxLimits,
    SandboxRequest,
    get_minimal_environment,
)
from executor.sandbox_audit import calculate_sha256_digest
from executor.sandbox_policy import SandboxPolicy
from executor.sandbox_runtime import DockerRuntimeBuilder
from executor.worker import (
    _fixed_url,
    _read_artifact,
    _validated_artifacts,
)

TEST_SANDBOX_IMAGE = os.environ.get(
    "CFT_SANDBOX_IMAGE",
    "python@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
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

# Раздел 1: Базовые тесты выполнения и контроля процессов

def test_process_sandbox_runs_fixed_worker_in_disposable_directory(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    sandbox = ProcessSandbox(workspace_root=workspace_root, limits=_limits())

    result = sandbox.run(_request())
    assert result.exit_code == 0
    assert result.stdout == "safe_noop:hello:outcome=confirmed"
    assert result.stderr == ""
    assert result.timed_out is False
    assert list(workspace_root.iterdir()) == []


def test_process_sandbox_timeout_is_a_result_not_an_exception(tmp_path: Path) -> None:
    worker_path = tmp_path / "hung_worker.py"
    worker_path.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
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


def test_process_sandbox_captures_stderr_and_nonzero_exit(tmp_path: Path) -> None:
    worker_path = tmp_path / "error_worker.py"
    worker_path.write_text("import sys\nprint('controlled failure', file=sys.stderr)\nraise SystemExit(7)\n", encoding="utf-8")
    sandbox = ProcessSandbox(workspace_root=tmp_path / "workspaces", limits=_limits(), worker_path=worker_path)

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
def test_process_sandbox_applies_os_resource_limits(tmp_path: Path) -> None:
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
    sandbox = ProcessSandbox(workspace_root=tmp_path / "workspaces", limits=limits, worker_path=worker_path, python_executable=sys.executable)
    result = sandbox.run(_request("limits-run"))
    applied = json.loads(result.stdout)
    assert result.exit_code == 0
    assert applied["cpu"] == limits.cpu_time_seconds
    assert applied["file"] == limits.max_file_bytes
    if sys.platform.startswith("linux"):
        assert applied["processes"] == limits.max_processes


def test_worker_never_uses_parameter_as_url(monkeypatch) -> None:
    called = False
    def fake_get(url: str, timeout: float, output_limit: int, request_host: str | None = None):
        nonlocal called
        called = True
        return 0, "{}", ""

    monkeypatch.setattr(worker, "_http_get", fake_get)
    result = worker._execute({
        "tool": "observe_http_surface", "base_url": "http://backend:8000",
        "endpoint": "/api/projects/",
        "parameters": {"url": "http://example.com"}, "request_timeout_seconds": 1, "max_output_bytes": 1024,
    })
    assert result[0] == 2
    assert called is False


def test_worker_dockerfile_check_never_accepts_agent_path_parameter(tmp_path: Path) -> None:
    dockerfile = tmp_path / "backend" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")
    result = worker._execute({
        "tool": "inspect_dockerfile_user", "repository_path": str(tmp_path),
        "artifacts": {"backend_dockerfile": {"kind": "dockerfile", "path": "backend/Dockerfile"}},
        "parameters": {"artifact_id": "backend_dockerfile", "path": "../../outside"},
        "request_timeout_seconds": 1, "max_output_bytes": 1024,
    })
    assert result[0] == 1
    assert "Unsupported capability parameters" in result[2]

# Раздел 2: Границы безопасности и правила изоляции (IR-01–IR-13)

def test_ir01_ephemeral_workspace_isolated_and_destroyed(tmp_path: Path) -> None:
    """IR-01: Временная рабочая область имеет уникальный идентификатор и уничтожается сразу после выхода."""
    workspace_root = tmp_path / "workspaces"
    sandbox = ProcessSandbox(workspace_root=workspace_root)
    req = _request("ir01-cleanup")
    result = sandbox.run(req)
    assert result.exit_code == 0
    assert not list(workspace_root.glob(f"{result.workspace_id}*"))


def test_ir02_environment_built_from_empty_dict_without_ci_secrets(monkeypatch) -> None:
    """IR-02: Минимальное окружение создается на основе пустого словаря; секреты не наследуются."""
    monkeypatch.setenv("CI_SECRET_API_TOKEN", "super_secret_val")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIAIOSFODNN7EXAMPLE")
    clean_env = get_minimal_environment()
    assert "CI_SECRET_API_TOKEN" not in clean_env
    assert "AWS_SECRET_ACCESS_KEY" not in clean_env
    assert set(clean_env.keys()).issubset(STRICT_ALLOWED_ENV | {"PYTHONHASHSEED"})


def test_ir02_worker_subprocess_dump_verifies_zero_host_secrets(tmp_path: Path, monkeypatch) -> None:
    """IR-02: Выполнение дочернего процесса подтверждает отсутствие утечки секретов хоста."""
    monkeypatch.setenv("TEST_CI_SECRET_LEAK", "SHOULD_NOT_BE_SEEN")
    dump_worker = tmp_path / "dump_env_worker.py"
    dump_worker.write_text(
        "import os, json\n"
        "env_dict = dict(os.environ)\n"
        "print(json.dumps({'has_leak': 'TEST_CI_SECRET_LEAK' in env_dict}))\n",
        encoding="utf-8",
    )
    sandbox = ProcessSandbox(workspace_root=tmp_path / "workspaces", worker_path=dump_worker)
    res = sandbox.run(_request("dump-env"))
    assert json.loads(res.stdout)["has_leak"] is False


def test_ir03_docker_runtime_builder_enforces_socket_and_root_isolation(
    tmp_path: Path,
) -> None:
    """IR-03: Команда среды выполнения Docker обеспечивает соблюдение условий изоляции."""

    policy = SandboxPolicy(
        backend="docker",
        network_mode="none",
        sandbox_image=TEST_SANDBOX_IMAGE,
    )
    builder = DockerRuntimeBuilder(policy)

    repo = tmp_path / "repo"
    repo.mkdir()

    worker_script = Path(worker.__file__).resolve()

    spec = builder.build_spec(
        run_id="sec-1",
        target_repo_host_path=repo,
        worker_script_host_path=worker_script,
        target_network_enabled=False,
    )

    argv = spec.argv
    cmd_str = " ".join(argv)

    assert "--read-only" in argv

    assert "--cap-drop" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"

    assert "--security-opt" in argv
    assert (
        argv[argv.index("--security-opt") + 1]
        == "no-new-privileges:true"
    )

    assert "--network" in argv
    assert argv[argv.index("--network") + 1] == "none"

    tmpfs = argv[argv.index("--tmpfs") + 1]
    assert tmpfs == "/workspace:rw,noexec,nosuid,nodev,size=67108864,uid=65534,gid=65534,mode=0700"

    assert "/var/run/docker.sock" not in cmd_str


def test_docker_runtime_tmpfs_uses_configured_non_root_user(tmp_path: Path) -> None:
    policy = SandboxPolicy(
        backend="docker",
        network_mode="none",
        sandbox_image=TEST_SANDBOX_IMAGE,
        container_user="10001:10002",
    )
    spec = DockerRuntimeBuilder(policy).build_spec(
        run_id="workspace-owner",
        target_repo_host_path=None,
        worker_script_host_path=Path(worker.__file__).resolve(),
    )
    tmpfs = spec.argv[spec.argv.index("--tmpfs") + 1]
    assert tmpfs == "/workspace:rw,noexec,nosuid,nodev,size=67108864,uid=10001,gid=10002,mode=0700"


def test_ir04_fixed_url_validation_blocks_invalid_targets() -> None:
    """IR-04: Проверка целевого URL строго блокирует передачу учетных данных и инъекции через параметры запроса."""
    with pytest.raises(ValueError, match="Invalid trusted target URL"):
        _fixed_url("http://user:pass@evil.com", "/health/")
    valid = _fixed_url("http://127.0.0.1:8000/", "/health/")
    assert valid == "http://127.0.0.1:8000/health/"


def test_ir05_wall_timeout_kills_process_group(tmp_path: Path) -> None:
    """IR-05: Превышение времени выполнения процесса приводит к завершению группы процессов и возврату кода завершения 124."""
    slow_worker = tmp_path / "slow_worker.py"
    slow_worker.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    sandbox = ProcessSandbox(workspace_root=tmp_path / "workspaces", limits=SandboxLimits(wall_time_seconds=0.2), worker_path=slow_worker)
    result = sandbox.run(_request("ir05-timeout"))
    assert result.timed_out is True
    assert result.exit_code == 124


def test_ir08_shell_metacharacters_not_evaluated(tmp_path: Path) -> None:
    """IR-08: при shell=False метасимволы оболочки (shell) интерпретируются как обычные строковые литералы."""
    sandbox = ProcessSandbox(workspace_root=tmp_path / "workspaces")
    result = sandbox.run(SandboxRequest(run_id="noshell", tool="safe_noop", base_url="http://127.0.0.1", parameters={"message": "$(echo PWNED)`whoami`"}, request_timeout_seconds=1.0))
    assert result.exit_code == 0
    assert "$(echo PWNED)`whoami`" in result.stdout


def test_ir09_memory_dos_stream_bounding(tmp_path: Path) -> None:
    """IR-09: Бесконечный вывод не приводит к исчерпанию памяти, а усекается"""
    loud_worker = tmp_path / "loud_worker.py"
    loud_worker.write_text("import sys\nsys.stdout.write('A' * 50000)\n", encoding="utf-8")
    max_bytes = 1024
    sandbox = ProcessSandbox(workspace_root=tmp_path / "workspaces", limits=SandboxLimits(max_output_bytes=max_bytes), worker_path=loud_worker)
    result = sandbox.run(_request("ir09-bounded"))
    assert "...[truncated]" in result.stdout
    assert len(result.stdout.encode("utf-8")) <= max_bytes + 50


def test_ir10_run_limits_action_replay_and_concurrency(tmp_path: Path) -> None:
    """IR-10: RunLimiter предотвращает повторное выполнение действий и ограничивает параллелизм"""
    limiter = RunLimiter(scope=tmp_path, max_runs_per_action=1, max_concurrent_runs=1)
    ok, _ = limiter.acquire("action-once")
    assert ok is True
    limiter.release()
    second_ok, reason = limiter.acquire("action-once")
    assert second_ok is False
    assert "run limit reached" in reason


def test_ir11_sha256_audit_digest_calculation() -> None:
    """IR-11: Дайджест аудита детерминированно вычисляет хеши SHA-256"""
    data = {"action_id": "test-123", "tool": "safe_noop"}
    digest1 = calculate_sha256_digest(data)
    digest2 = calculate_sha256_digest(data)
    assert digest1 == digest2
    assert len(digest1) == 64


def test_ir12_path_traversal_out_of_repo_rejected() -> None:
    """IR-12: Определения целевых артефактов, содержащие «..», отклоняются"""
    with pytest.raises(ValueError, match="Malformed trusted target artifact"):
        _validated_artifacts({"evil": {"kind": "python", "path": "../outside.py"}})


def test_ir13_symlink_escape_attack_prevented(tmp_path: Path) -> None:
    """IR-13: Символические ссылки, выходящие за пределы целевого репозитория, строго блокируются функцией _read_artifact"""
    repo = tmp_path / "target_repo"
    secret_dir = tmp_path / "host_secrets"
    repo.mkdir()
    secret_dir.mkdir()
    secret_file = secret_dir / "id_rsa"
    secret_file.write_text("HOST_PRIVATE_KEY", encoding="utf-8")

    symlink_file = repo / "symlink_to_secrets.py"
    try:
        symlink_file.symlink_to(secret_file)
    except OSError:
        pytest.skip("Symlinks not supported on this filesystem")

    validated = _validated_artifacts({"symlink_artifact": {"kind": "python", "path": "symlink_to_secrets.py"}})
    with pytest.raises(ValueError, match="escaped target repository"):
        _read_artifact(repository_path=str(repo), artifacts=validated, artifact_id="symlink_artifact", expected_kind="python")

def test_unknown_capability_fails_closed() -> None:
    result = worker._execute(
        {
            "tool": "future_network_tool",
            "base_url": "http://127.0.0.1:8000",
            "parameters": {},
            "request_timeout_seconds": 1.0,
            "max_output_bytes": 1024,
        }
    )

    assert result[0] != 0
    assert result[1] == ""
    assert "unknown worker capability" in result[2].lower()

def test_artifact_capability_has_no_network_access() -> None:
    assert (
        CAPABILITY_NETWORK_ACCESS[
            "inspect_dockerfile_user"
        ]
        == "none"
    )

def test_http_capability_has_target_only_network_access() -> None:
    assert (
        CAPABILITY_NETWORK_ACCESS[
            "observe_http_surface"
        ]
        == "target"
    )

def test_docker_runtime_does_not_mount_any_host_repository_when_not_required(
    tmp_path: Path,
) -> None:
    policy = SandboxPolicy(
        backend="docker",
        network_mode="none",
        sandbox_image=TEST_SANDBOX_IMAGE,
    )
    builder = DockerRuntimeBuilder(policy)

    worker_script = Path(worker.__file__).resolve()

    spec = builder.build_spec(
        run_id="no-repo",
        target_repo_host_path=None,
        worker_script_host_path=worker_script,
        target_network_enabled=False,
    )

    command = " ".join(spec.argv)

    assert "/target" not in command
    assert "/tmp:/target" not in command

def test_docker_runtime_mounts_only_explicit_trusted_repository_read_only(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "trusted-repo"
    repo.mkdir()

    policy = SandboxPolicy(
        backend="docker",
        network_mode="none",
        sandbox_image=TEST_SANDBOX_IMAGE,
    )
    builder = DockerRuntimeBuilder(policy)

    worker_script = Path(worker.__file__).resolve()

    spec = builder.build_spec(
        run_id="repo",
        target_repo_host_path=repo,
        worker_script_host_path=worker_script,
        target_network_enabled=False,
    )

    expected_mount = f"{repo.resolve()}:/target:ro"

    assert expected_mount in spec.argv

    target_mounts = [
        value
        for value in spec.argv
        if ":/target" in value
    ]

    assert target_mounts == [expected_mount]


def test_worker_runs_generic_command_only_in_declared_lab_directory(tmp_path: Path) -> None:
    source = tmp_path / "marker.txt"
    source.write_text("sandbox-visible", encoding="utf-8")

    code, stdout, stderr = worker._execute(
        {
            "tool": "sandbox_command",
            "repository_path": str(tmp_path),
            "parameters": {
                "argv": [sys.executable, "-c", "from pathlib import Path; print(Path('marker.txt').read_text())"],
                "cwd": "/target",
            },
            "request_timeout_seconds": 1.0,
            "max_output_bytes": 1024,
        }
    )

    assert code == 0
    assert stdout.strip() == "sandbox-visible"
    assert stderr == ""


def test_sandbox_command_capability_has_no_network_access() -> None:
    assert CAPABILITY_NETWORK_ACCESS["sandbox_command"] == "none"
