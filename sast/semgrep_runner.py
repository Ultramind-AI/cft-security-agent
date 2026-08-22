from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sast.normalizer import normalize_semgrep_payload
from schemas.finding import Finding


class SemgrepError(RuntimeError):
    """Ошибка, когда локальный скан Semgrep не удалось запустить или завершить."""


@dataclass(frozen=True)
class SemgrepScanResult:
    target: Path
    config: str
    raw: dict[str, Any]
    findings: list[Finding]
    stderr: str


def run_semgrep_scan(
    target: str | Path,
    *,
    config: str = "auto",
    timeout_seconds: int = 600,
    service_resolver: Callable[[str], str | None] | None = None,
) -> SemgrepScanResult:
    """Запустить скан SAST Semgrep только для чтения по локальному дереву исходников.

    Таргет используется только как рабочий каталог процесса. Сервер приложения не
    запускается, и этот код не отправляет сетевые запросы к таргету.
    """
    target_path = Path(target).expanduser().resolve()
    if not target_path.is_dir():
        raise SemgrepError(f"SAST target directory does not exist: {target_path}")

    executable = shutil.which("semgrep")
    if executable is None:
        raise SemgrepError(
            "Semgrep CLI was not found. Activate the project venv and install "
            'the SAST extra: python -m pip install -e ".[dev,sast]"'
        )

    command = [
        executable,
        "scan",
        "--config",
        config,
        "--json",
        "--exclude",
        ".venv",
        "--exclude",
        "node_modules",
        "--exclude",
        "dist",
        "--exclude",
        "build",
        ".",
    ]

    # SAST запускается только для чтения в каталоге таргета
    try:
        completed = subprocess.run(
            command,
            cwd=target_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SemgrepError(f"Semgrep timed out after {timeout_seconds} seconds") from exc
    except OSError as exc:
        raise SemgrepError(f"Failed to start Semgrep: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SemgrepError(
            f"Semgrep exited with code {completed.returncode}: {detail[:2000]}"
        )

    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SemgrepError("Semgrep did not return valid JSON") from exc

    if not isinstance(raw, dict):
        raise SemgrepError("Semgrep JSON root must be an object")

    return SemgrepScanResult(
        target=target_path,
        config=config,
        raw=raw,
        findings=normalize_semgrep_payload(raw, service_resolver=service_resolver),
        stderr=completed.stderr,
    )
