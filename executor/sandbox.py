import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import ClassVar, Protocol


@dataclass(frozen=True)
class SandboxLimits:
    wall_time_seconds: float = 5.0
    cpu_time_seconds: int = 2
    memory_bytes: int = 256 * 1024 * 1024
    max_file_bytes: int = 1024 * 1024
    max_processes: int = 8
    max_output_bytes: int = 16_384

    def __post_init__(self) -> None:
        values = {
            "wall_time_seconds": self.wall_time_seconds,
            "cpu_time_seconds": self.cpu_time_seconds,
            "memory_bytes": self.memory_bytes,
            "max_file_bytes": self.max_file_bytes,
            "max_processes": self.max_processes,
            "max_output_bytes": self.max_output_bytes,
        }
        if any(value <= 0 for value in values.values()):
            raise ValueError("All sandbox limits must be positive")


@dataclass(frozen=True)
class SandboxRequest:
    run_id: str
    tool: str
    base_url: str
    parameters: dict
    request_timeout_seconds: float


@dataclass(frozen=True)
class SandboxResult:
    run_id: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    workspace_id: str


class Sandbox(Protocol):
    def run(self, request: SandboxRequest) -> SandboxResult: ...


class RunLimiter:
    """Bound starts and parallel executions for one trusted process."""

    _shared: ClassVar[dict[tuple[str, int, int], "RunLimiter"]] = {}
    _shared_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        *,
        max_runs_per_action: int = 1,
        max_concurrent_runs: int = 1,
    ) -> None:
        if max_runs_per_action <= 0 or max_concurrent_runs <= 0:
            raise ValueError("Run limits must be positive")
        self.max_runs_per_action = max_runs_per_action
        self.max_concurrent_runs = max_concurrent_runs
        self._counts: dict[str, int] = {}
        self._semaphore = threading.BoundedSemaphore(max_concurrent_runs)
        self._lock = threading.Lock()

    @classmethod
    def shared(
        cls,
        *,
        scope: str | Path,
        max_runs_per_action: int,
        max_concurrent_runs: int,
    ) -> "RunLimiter":
        key = (
            str(Path(scope).resolve()),
            max_runs_per_action,
            max_concurrent_runs,
        )
        with cls._shared_lock:
            if key not in cls._shared:
                cls._shared[key] = cls(
                    max_runs_per_action=max_runs_per_action,
                    max_concurrent_runs=max_concurrent_runs,
                )
            return cls._shared[key]

    def acquire(self, action_id: str) -> tuple[bool, str]:
        with self._lock:
            count = self._counts.get(action_id, 0)
            if count >= self.max_runs_per_action:
                return (
                    False,
                    f"Action run limit reached ({self.max_runs_per_action})",
                )
            if not self._semaphore.acquire(blocking=False):
                return (
                    False,
                    f"Concurrent run limit reached ({self.max_concurrent_runs})",
                )
            self._counts[action_id] = count + 1
        return True, "Run slot acquired"

    def release(self) -> None:
        self._semaphore.release()


class ProcessSandbox:
    """Run the fixed executor worker with bounded OS resources and no shell."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        limits: SandboxLimits,
        worker_path: str | Path | None = None,
        python_executable: str | Path | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.limits = limits
        self.worker_path = Path(
            worker_path or Path(__file__).with_name("worker.py")
        ).resolve()
        self.python_executable = str(python_executable or sys.executable)

    def run(self, request: SandboxRequest) -> SandboxResult:
        started = perf_counter()
        workspace_id = f"run-{request.run_id}"
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        try:
            with tempfile.TemporaryDirectory(
                prefix=f"{workspace_id}-",
                dir=self.workspace_root,
            ) as temporary_directory:
                workspace = Path(temporary_directory)
                stdout_path = workspace / "stdout.txt"
                stderr_path = workspace / "stderr.txt"
                payload = json.dumps(
                    {
                        "run_id": request.run_id,
                        "tool": request.tool,
                        "base_url": request.base_url,
                        "parameters": request.parameters,
                        "request_timeout_seconds": request.request_timeout_seconds,
                        "max_output_bytes": self.limits.max_output_bytes,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")

                timed_out = False
                with stdout_path.open("wb") as stdout_file, stderr_path.open(
                    "wb"
                ) as stderr_file:
                    process = subprocess.Popen(
                        [self.python_executable, str(self.worker_path)],
                        cwd=workspace,
                        env=_minimal_environment(),
                        stdin=subprocess.PIPE,
                        stdout=stdout_file,
                        stderr=stderr_file,
                        shell=False,
                        start_new_session=os.name == "posix",
                        preexec_fn=(  # noqa: PLW1509 - POSIX sandbox limits.
                            _resource_limiter(self.limits)
                            if os.name == "posix"
                            else None
                        ),
                    )
                    try:
                        process.communicate(
                            input=payload,
                            timeout=self.limits.wall_time_seconds,
                        )
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        _kill_process_tree(process)
                        process.communicate()

                stdout = _read_bounded(
                    stdout_path,
                    self.limits.max_output_bytes,
                )
                stderr = _read_bounded(
                    stderr_path,
                    self.limits.max_output_bytes,
                )
                exit_code = process.returncode
                if timed_out:
                    exit_code = 124
                    timeout_message = (
                        "Execution timed out after "
                        f"{self.limits.wall_time_seconds:g} seconds"
                    )
                    stderr = _append_bounded(
                        stderr,
                        timeout_message,
                        self.limits.max_output_bytes,
                    )

                return SandboxResult(
                    run_id=request.run_id,
                    exit_code=int(exit_code),
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=int((perf_counter() - started) * 1000),
                    timed_out=timed_out,
                    workspace_id=workspace_id,
                )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return SandboxResult(
                run_id=request.run_id,
                exit_code=127,
                stdout="",
                stderr=f"Sandbox setup failed: {type(exc).__name__}",
                duration_ms=int((perf_counter() - started) * 1000),
                timed_out=False,
                workspace_id=workspace_id,
            )


def _minimal_environment() -> dict[str, str]:
    environment = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
    }
    for name in ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _resource_limiter(limits: SandboxLimits):
    def apply_limits() -> None:
        import resource

        os.umask(0o077)
        cpu_limit = max(1, math.ceil(limits.cpu_time_seconds))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
        resource.setrlimit(
            resource.RLIMIT_AS,
            (limits.memory_bytes, limits.memory_bytes),
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (limits.max_file_bytes, limits.max_file_bytes),
        )
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(
                resource.RLIMIT_NPROC,
                (limits.max_processes, limits.max_processes),
            )

    return apply_limits


def _kill_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    process.kill()


def _read_bounded(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    truncated = len(data) > limit
    decoded = data[:limit].decode("utf-8", errors="replace")
    if truncated:
        return f"{decoded}\n...[truncated]"
    return decoded


def _append_bounded(current: str, addition: str, limit: int) -> str:
    combined = f"{current}\n{addition}".strip()
    encoded = combined.encode("utf-8")
    if len(encoded) <= limit:
        return combined
    return f"{encoded[:limit].decode('utf-8', errors='replace')}\n...[truncated]"
