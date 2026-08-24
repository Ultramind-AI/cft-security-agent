"""Cooperative cancellation shared by API, pipeline and sandbox code."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event


class RunCancelled(RuntimeError):
    """The operator cancelled an analysis run."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RunCancelled("Analysis run was cancelled")


_ACTIVE_TOKEN: ContextVar[CancellationToken | None] = ContextVar(
    "cft_active_cancellation_token",
    default=None,
)


def check_cancelled(token: CancellationToken | None = None) -> None:
    active = token or _ACTIVE_TOKEN.get()
    if active is not None:
        active.raise_if_cancelled()


def current_token() -> CancellationToken | None:
    return _ACTIVE_TOKEN.get()


@contextmanager
def cancellation_scope(token: CancellationToken | None) -> Iterator[None]:
    marker = _ACTIVE_TOKEN.set(token)
    try:
        check_cancelled(token)
        yield
    finally:
        _ACTIVE_TOKEN.reset(marker)


@contextmanager
def suspend_cancellation() -> Iterator[None]:
    """Allow mandatory cleanup commands to run after cancellation."""

    marker = _ACTIVE_TOKEN.set(None)
    try:
        yield
    finally:
        _ACTIVE_TOKEN.reset(marker)
