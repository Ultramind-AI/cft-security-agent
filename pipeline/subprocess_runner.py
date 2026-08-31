"""Ограниченный запуск subprocess с timeout и cooperative cancellation"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from pipeline.cancellation import CancellationToken, RunCancelled, current_token


def run_cancellable_process(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    timeout: float,
    environment: Mapping[str, str] | None = None,
    cancellation_token: CancellationToken | None = None,
    poll_interval: float = 0.1,
    terminate_grace: float = 2.0,
) -> subprocess.CompletedProcess[str]:
    if timeout <= 0:
        raise ValueError("subprocess timeout must be positive")
    token = cancellation_token or current_token()
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(environment) if environment is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        start_new_session=(os.name != "nt"),
    )
    started = time.monotonic()
    while True:
        if token is not None and token.is_cancelled():
            _stop_process(process, terminate_grace)
            process.communicate()
            raise RunCancelled("Analysis run was cancelled while a command was running")
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            _stop_process(process, terminate_grace)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(list(argv), timeout, output=stdout, stderr=stderr)
        try:
            stdout, stderr = process.communicate(timeout=min(poll_interval, remaining))
            return subprocess.CompletedProcess(list(argv), process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            continue


def _stop_process(process: subprocess.Popen[str], grace: float) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=grace)
