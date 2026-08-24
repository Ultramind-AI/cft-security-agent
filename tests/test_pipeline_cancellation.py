from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from threading import Thread

import pytest

from executor.sandbox import _communicate_bounded
from pipeline.cancellation import CancellationToken, RunCancelled, cancellation_scope
from pipeline.subprocess_runner import run_cancellable_process


def test_cancellable_process_returns_structured_output(tmp_path: Path) -> None:
    result = run_cancellable_process(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        cwd=tmp_path,
        timeout=2,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_cancellable_process_stops_running_child(tmp_path: Path) -> None:
    token = CancellationToken()

    def cancel() -> None:
        token.cancel()

    thread = Thread(target=cancel)
    thread.start()
    with pytest.raises(RunCancelled):
        run_cancellable_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout=60,
            cancellation_token=token,
        )
    thread.join(1)


def test_cancellable_process_preserves_timeout(tmp_path: Path) -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        run_cancellable_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout=0.05,
            poll_interval=0.01,
        )


def test_context_token_is_optional(tmp_path: Path) -> None:
    token = CancellationToken()
    with cancellation_scope(token):
        result = run_cancellable_process(
            [sys.executable, "-c", "print('ok')"],
            cwd=tmp_path,
            timeout=2,
        )
    assert result.stdout.strip() == "ok"


def test_sandbox_process_observes_shared_cancellation_token() -> None:
    token = CancellationToken()
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(sys.platform != "win32"),
    )
    with cancellation_scope(token):
        token.cancel()
        with pytest.raises(RunCancelled):
            _communicate_bounded(process, b"", 60, 1024)
    assert process.poll() is not None
