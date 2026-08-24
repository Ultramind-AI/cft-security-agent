from __future__ import annotations

from threading import Event, Thread

from api.scheduler import ResourceBudget, ResourceRequest, RunScheduler
from pipeline.cancellation import CancellationToken, RunCancelled


def test_scheduler_is_fifo_with_one_worker() -> None:
    scheduler = RunScheduler(workers=1)
    release = Event()
    done = Event()
    order: list[str] = []

    def first(_: CancellationToken) -> None:
        order.append("first")
        release.wait(2)

    def second(_: CancellationToken) -> None:
        order.append("second")
        done.set()

    scheduler.submit("one", first)
    scheduler.submit("two", second)
    release.set()
    assert done.wait(2)
    scheduler.close()
    assert order == ["first", "second"]


def test_cancelled_queued_job_is_not_started() -> None:
    scheduler = RunScheduler(workers=1)
    release = Event()
    called = Event()
    scheduler.submit("one", lambda _: release.wait(2))
    scheduler.submit("two", lambda _: called.set())
    assert scheduler.cancel("two") is True
    release.set()
    scheduler.close()
    assert called.is_set() is False


def test_resource_lease_is_idempotent() -> None:
    budget = ResourceBudget(sandboxes=1, cpu=1.0, memory_mb=256)
    token = CancellationToken()
    lease = budget.acquire(ResourceRequest(1, 1.0, 256), token)
    lease.release()
    lease.release()
    assert budget.used == ResourceRequest(0, 0.0, 0)


def test_waiting_budget_acquire_observes_cancel() -> None:
    budget = ResourceBudget(sandboxes=1, cpu=1.0, memory_mb=256)
    request = ResourceRequest(1, 1.0, 256)
    first = budget.acquire(request, CancellationToken())
    token = CancellationToken()
    cancelled = Event()

    def wait_for_budget() -> None:
        try:
            budget.acquire(request, token)
        except RunCancelled:
            cancelled.set()

    thread = Thread(target=wait_for_budget)
    thread.start()
    token.cancel()
    budget.wake()
    assert cancelled.wait(2)
    first.release()
    thread.join(2)
    assert budget.used == ResourceRequest(0, 0.0, 0)
