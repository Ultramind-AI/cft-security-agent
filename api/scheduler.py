"""Small FIFO scheduler and shared resource budget for API analysis runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from queue import Queue
from threading import Condition, Lock, Thread
from typing import Self

from pipeline.cancellation import CancellationToken, RunCancelled


@dataclass(frozen=True)
class ResourceRequest:
    sandboxes: int = 1
    cpu: float = 1.0
    memory_mb: int = 512


class ResourceLease:
    def __init__(self, budget: ResourceBudget, request: ResourceRequest) -> None:
        self._budget = budget
        self.request = request
        self._released = False
        self._lock = Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._budget._release(self.request)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class ResourceBudget:
    def __init__(self, *, sandboxes: int, cpu: float, memory_mb: int) -> None:
        if sandboxes < 1 or cpu <= 0 or memory_mb < 1:
            raise ValueError("Resource budget values must be positive")
        self._capacity = ResourceRequest(sandboxes, cpu, memory_mb)
        self._used = ResourceRequest(0, 0.0, 0)
        self._condition = Condition()

    @property
    def used(self) -> ResourceRequest:
        with self._condition:
            return self._used

    def acquire(
        self,
        request: ResourceRequest,
        token: CancellationToken,
    ) -> ResourceLease:
        self._validate_request(request)
        with self._condition:
            while not self._fits(request):
                token.raise_if_cancelled()
                self._condition.wait(timeout=0.1)
            token.raise_if_cancelled()
            self._used = ResourceRequest(
                self._used.sandboxes + request.sandboxes,
                self._used.cpu + request.cpu,
                self._used.memory_mb + request.memory_mb,
            )
        return ResourceLease(self, request)

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def _fits(self, request: ResourceRequest) -> bool:
        return (
            self._used.sandboxes + request.sandboxes <= self._capacity.sandboxes
            and self._used.cpu + request.cpu <= self._capacity.cpu
            and self._used.memory_mb + request.memory_mb <= self._capacity.memory_mb
        )

    def _validate_request(self, request: ResourceRequest) -> None:
        if request.sandboxes < 0 or request.cpu < 0 or request.memory_mb < 0:
            raise ValueError("Resource request cannot be negative")
        if (
            request.sandboxes > self._capacity.sandboxes
            or request.cpu > self._capacity.cpu
            or request.memory_mb > self._capacity.memory_mb
        ):
            raise ValueError("Resource request exceeds the global capacity")

    def _release(self, request: ResourceRequest) -> None:
        with self._condition:
            self._used = ResourceRequest(
                max(0, self._used.sandboxes - request.sandboxes),
                max(0.0, self._used.cpu - request.cpu),
                max(0, self._used.memory_mb - request.memory_mb),
            )
            self._condition.notify_all()


RunCallback = Callable[[CancellationToken], None]


@dataclass
class _Job:
    run_id: str
    callback: RunCallback
    token: CancellationToken


class RunScheduler:
    """FIFO queue with a fixed number of worker threads."""

    def __init__(self, *, workers: int) -> None:
        if workers < 1:
            raise ValueError("workers must be positive")
        self._queue: Queue[_Job | None] = Queue()
        self._tokens: dict[str, CancellationToken] = {}
        self._lock = Lock()
        self._accepting = True
        self._threads = [
            Thread(target=self._worker, name=f"cft-api-run-{index}", daemon=True)
            for index in range(workers)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, run_id: str, callback: RunCallback) -> CancellationToken:
        with self._lock:
            if not self._accepting:
                raise RuntimeError("Run scheduler is closed")
            if run_id in self._tokens:
                raise ValueError(f"Run is already scheduled: {run_id}")
            token = CancellationToken()
            self._tokens[run_id] = token
        self._queue.put(_Job(run_id, callback, token))
        return token

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(run_id)
        if token is None:
            return False
        token.cancel()
        return True

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            if not self._accepting:
                return
            self._accepting = False
            tokens = list(self._tokens.values())
        for token in tokens:
            token.cancel()
        for _ in self._threads:
            self._queue.put(None)
        if wait:
            for thread in self._threads:
                thread.join(timeout=10.0)

    @property
    def alive_workers(self) -> int:
        return sum(thread.is_alive() for thread in self._threads)

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                if not job.token.is_cancelled():
                    try:
                        job.callback(job.token)
                    except RunCancelled:
                        pass
            finally:
                if job is not None:
                    with self._lock:
                        self._tokens.pop(job.run_id, None)
                self._queue.task_done()
