from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from executor.sandbox_policy import SandboxLimits, SandboxPolicy
from executor.sandbox_runtime import DockerRuntimeBuilder

logger = logging.getLogger(__name__)

NetworkAccess = Literal["none", "target"]

try:
    import resource
except ImportError:
    resource = None


@dataclass(frozen=True)
class SandboxRequest:
    run_id: str
    tool: str
    base_url: str
    parameters: dict
    request_timeout_seconds: float
    network_access: Literal["none", "target"] = "none"
    repository_path: str = ""
    artifacts: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxResult:
    """Структурированный результат, полученный в результате выполнения в «песочнице»"""
    run_id: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    workspace_id: str
    duration_ms: int = 0
    runtime_backend: str = "process"


class Sandbox(Protocol):
    def run(self, request: SandboxRequest) -> SandboxResult: ...

# Потоковый ридер с защитой от DoS-атак, использующих память

def _communicate_bounded(
    proc: subprocess.Popen,
    input_data: bytes,
    timeout_s: float,
    max_bytes: int,
) -> tuple[bytes, bool, bytes, bool, bool]:
    """
    Считывает вывод процесса с жестким ограничением объема данных (в байтах) для предотвращения DoS-атак, исчерпывающих память.
    Возвращает (raw_stdout, stdout_truncated, raw_stderr, stderr_truncated, timed_out)
    """
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_state = [0, False]  # [bytes_collected, was_truncated]
    stderr_state = [0, False]
    timed_out = False

    def feed_stdin():
        try:
            if proc.stdin:
                proc.stdin.write(input_data)
                proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    def read_stream(stream, chunks_list, state):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                if state[0] < max_bytes:
                    allowed = max_bytes - state[0]
                    chunks_list.append(chunk[:allowed])
                    state[0] += len(chunk[:allowed])
                    if len(chunk) > allowed:
                        state[1] = True
                else:
                    state[1] = True
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    t_stdin = threading.Thread(target=feed_stdin, daemon=True)
    t_stdout = threading.Thread(target=read_stream, args=(proc.stdout, stdout_chunks, stdout_state), daemon=True)
    t_stderr = threading.Thread(target=read_stream, args=(proc.stderr, stderr_chunks, stderr_state), daemon=True)

    t_stdin.start()
    t_stdout.start()
    t_stderr.start()

    start = time.monotonic()
    while True:
        if proc.poll() is not None:
            break
        if time.monotonic() - start > timeout_s:
            timed_out = True
            break
        time.sleep(0.02)

    if timed_out:
        if os.name == "posix" and hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass
        else:
            proc.kill()

    t_stdout.join(timeout=1.0)
    t_stderr.join(timeout=1.0)

    raw_stdout = b"".join(stdout_chunks)
    raw_stderr = b"".join(stderr_chunks)

    return raw_stdout, stdout_state[1], raw_stderr, stderr_state[1], timed_out


def _format_bounded_output(data: bytes, was_truncated: bool) -> str:
    text = data.decode("utf-8", errors="replace")
    if was_truncated:
        return f"{text}\n...[truncated]"
    return text

# Минимальное чистое окружение

STRICT_ALLOWED_ENV = frozenset({
    "PATH", "LANG", "LC_ALL", "PYTHONIOENCODING",
    "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED",
    "SYSTEMROOT", "SystemRoot", "WINDIR"
})

BLOCKED_SECRET_PATTERNS = (
    "TOKEN", "SECRET", "KEY", "PASS", "CREDENTIAL", "AUTH",
    "SSH", "AWS", "GITHUB", "GITLAB", "PRIVATE", "SESSION"
)


def get_minimal_environment(source_env: dict[str, str] | None = None) -> dict[str, str]:
    src = source_env if source_env is not None else os.environ
    clean_env: dict[str, str] = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "random",
    }
    for key in STRICT_ALLOWED_ENV:
        if key in src and not any(
            pattern in key.upper() for pattern in BLOCKED_SECRET_PATTERNS
        ):
            clean_env[key] = src[key]
    return clean_env


def _apply_posix_resource_limits(limits: SandboxLimits) -> None:
    os.umask(limits.umask)
    if hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass
    if resource is None:
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_time_seconds, limits.cpu_time_seconds + 1))
    except (ValueError, OSError):
        pass
    if hasattr(resource, "RLIMIT_AS") and sys.platform.startswith("linux"):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        except (ValueError, OSError):
            pass
    if hasattr(resource, "RLIMIT_FSIZE"):
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_file_bytes, limits.max_file_bytes))
        except (ValueError, OSError):
            pass
    if hasattr(resource, "RLIMIT_NPROC"):
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
        except (ValueError, OSError):
            pass

# RunLimiter

class RunLimiter:
    _instances: ClassVar[dict[str, RunLimiter]] = {}
    _global_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, scope: str | Path | None = None, max_runs_per_action: int = 1, max_concurrent_runs: int = 1) -> None:
        self.scope = str(scope or "default")
        self.max_runs_per_action = max_runs_per_action
        self.max_concurrent_runs = max_concurrent_runs
        self._action_counts: dict[str, int] = defaultdict(int)
        self._semaphore = threading.BoundedSemaphore(max_concurrent_runs)
        self._lock = threading.Lock()

    @classmethod
    def shared(cls, scope: str | Path, max_runs_per_action: int = 1, max_concurrent_runs: int = 1) -> RunLimiter:
        key = str(Path(scope).resolve())
        with cls._global_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(scope=key, max_runs_per_action=max_runs_per_action, max_concurrent_runs=max_concurrent_runs)
            return cls._instances[key]

    def acquire(self, action_id: str) -> tuple[bool, str]:
        with self._lock:
            if self._action_counts[action_id] >= self.max_runs_per_action:
                return False, (
                    f"Action run limit reached: action '{action_id}' "
                    f"reached max execution limit ({self.max_runs_per_action})"
                )
            self._action_counts[action_id] += 1
        if not self._semaphore.acquire(blocking=False):
            with self._lock:
                self._action_counts[action_id] -= 1
            return False, "Concurrent run limit reached. Action blocked."
        return True, ""

    def release(self) -> None:
        try:
            self._semaphore.release()
        except ValueError:
            pass


class ProcessSandbox:

    def __init__(
        self,
        workspace_root: str | Path,
        limits: SandboxLimits | None = None,
        worker_path: str | Path | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.limits = limits or SandboxLimits()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.python_executable = python_executable or sys.executable
        self.worker_path = Path(worker_path).resolve() if worker_path else (Path(__file__).parent / "worker.py").resolve()

    def run(self, request: SandboxRequest) -> SandboxResult:
        unique_suffix = uuid.uuid4().hex[:8]
        workspace_id = f"run-{request.run_id}-{unique_suffix}"
        workspace_dir = self.workspace_root / workspace_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "tool": request.tool,
            "base_url": request.base_url,
            "parameters": request.parameters,
            "request_timeout_seconds": request.request_timeout_seconds,
            "repository_path": request.repository_path,
            "artifacts": request.artifacts,
            "max_output_bytes": self.limits.max_output_bytes,
        }
        input_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        env = get_minimal_environment()
        start_time = time.perf_counter()

        preexec = (lambda: _apply_posix_resource_limits(self.limits)) if os.name == "posix" else None

        try:
            proc = subprocess.Popen(
                [self.python_executable, str(self.worker_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(workspace_dir),
                env=env,
                shell=False,
                preexec_fn=preexec,  # noqa: PLW1509 -- needed for POSIX rlimits
            )

            raw_stdout, stdout_trunc, raw_stderr, stderr_trunc, timed_out = _communicate_bounded(
                proc, input_data, self.limits.wall_time_seconds, self.limits.max_output_bytes
            )
            exit_code = 124 if timed_out else (proc.returncode if proc.returncode is not None else 1)

            if timed_out:
                raw_stderr = f"Process timed out after {self.limits.wall_time_seconds}s\n".encode() + raw_stderr

            stdout_str = _format_bounded_output(raw_stdout, stdout_trunc)
            stderr_str = _format_bounded_output(raw_stderr, stderr_trunc)

        except Exception as exc:  # noqa: BLE001 -- sandbox errors become structured results
            stdout_str = ""
            stderr_str = f"Process sandbox execution failed: {type(exc).__name__}: {exc}"
            exit_code = 127
            timed_out = False
        finally:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            if workspace_dir.exists():
                shutil.rmtree(workspace_dir, ignore_errors=True)

        return SandboxResult(
            run_id=request.run_id,
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            timed_out=timed_out,
            workspace_id=workspace_id,
            duration_ms=duration_ms,
            runtime_backend="process",
        )

# Граница безопасности контейнеров в продакшене и CI

class DockerSandbox:
    """Песочница для обеспечения безопасности контейнеров, принудительно устанавливающая режим «только для чтения» для корневой файловой системы, сетевую изоляцию и квоты для tmpfs"""

    def __init__(self, policy: SandboxPolicy, worker_path: str | Path | None = None) -> None:
        self.policy = policy
        self.worker_path = Path(worker_path).resolve() if worker_path else (Path(__file__).parent / "worker.py").resolve()
        self.builder = DockerRuntimeBuilder(policy)

    def run(self, request: SandboxRequest) -> SandboxResult:
        workspace_id = f"run-{request.run_id}-{uuid.uuid4().hex}"

        repo_path: Path | None = None
        if request.repository_path:
            repo_path = Path(request.repository_path).resolve()

            if not repo_path.is_dir():
                raise ValueError(
                    f"Trusted repository path is not a directory: {repo_path}"
                )

        spec = self.builder.build_spec(
            run_id=workspace_id,
            target_repo_host_path=repo_path,
            worker_script_host_path=self.worker_path,
            target_network_enabled=request.network_access == "target",
        )

        payload = {
            "tool": request.tool,
            "base_url": request.base_url,
            "parameters": request.parameters,
            "request_timeout_seconds": request.request_timeout_seconds,
            "repository_path": "/target" if repo_path is not None else "",
            "artifacts": request.artifacts,
            "max_output_bytes": self.policy.limits.max_output_bytes,
        }
        input_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        start_time = time.perf_counter()

        try:
            proc = subprocess.Popen(
                spec.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )

            raw_stdout, stdout_trunc, raw_stderr, stderr_trunc, timed_out = _communicate_bounded(
                proc, input_data, self.policy.limits.wall_time_seconds, self.policy.limits.max_output_bytes
            )
            exit_code = 124 if timed_out else (proc.returncode if proc.returncode is not None else 1)

            if timed_out:
                subprocess.run(["docker", "rm", "-f", spec.container_name], capture_output=True, check=False)
                raw_stderr = f"Container timed out after {self.policy.limits.wall_time_seconds}s\n".encode() + raw_stderr

            stdout_str = _format_bounded_output(raw_stdout, stdout_trunc)
            stderr_str = _format_bounded_output(raw_stderr, stderr_trunc)

        except Exception as exc:  # noqa: BLE001 -- sandbox errors become structured results
            stdout_str = ""
            stderr_str = f"Docker container launch failed: {type(exc).__name__}: {exc}"
            exit_code = 127
            timed_out = False
        finally:
            duration_ms = int((time.perf_counter() - start_time) * 1000)

        return SandboxResult(
            run_id=request.run_id,
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            timed_out=timed_out,
            workspace_id=spec.workspace_id,
            duration_ms=duration_ms,
            runtime_backend="docker",
        )
