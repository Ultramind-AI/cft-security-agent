"""Append-only JSONL журнал прогресса одного запуска

Это best-effort observability канал для live UI, он не влияет на решения пайплайна
Ошибки записи не ломают основной flow. API читает журнал вместе с audit log Executor
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

PROGRESS_FILE_NAME = "progress.jsonl"

_STAGE_DISCOVERY = "discovery"
_STAGE_SANDBOX = "sandbox"
_STAGE_SAST = "sast"
_STAGE_VERIFICATION = "verification"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PipelineProgressRecorder:
    """Пишем компактное JSONL событие на каждой границе stage"""

    def __init__(self, output_dir: str | Path) -> None:
        self.path = Path(output_dir) / PROGRESS_FILE_NAME
        self._lock = Lock()

    def stage(self, stage: str, *, status: str, detail: str | None = None) -> None:
        payload: dict[str, object] = {
            "ts": _now(),
            "kind": "stage",
            "stage": stage,
            "status": status,
        }
        if detail:
            payload["detail"] = detail[:500]
        self._append(payload)

    def discovery_done(self, detail: str | None = None) -> None:
        self.stage(_STAGE_DISCOVERY, status="done", detail=detail)

    def sandbox(self, *, status: str, detail: str | None = None) -> None:
        self.stage(_STAGE_SANDBOX, status=status, detail=detail)

    def sast_done(self, finding_count: int) -> None:
        self.stage(_STAGE_SAST, status="done", detail=f"{finding_count} findings")

    def verification_started(self) -> None:
        self.stage(_STAGE_VERIFICATION, status="running")

    def finding_started(
        self,
        *,
        index: int,
        total: int,
        finding_id: str,
        title: str,
        severity: str | None,
        rule_id: str,
        file: str,
    ) -> None:
        self._append(
            {
                "ts": _now(),
                "kind": "finding_started",
                "index": index,
                "total": total,
                "finding_id": finding_id,
                "title": title[:300],
                "severity": severity,
                "rule_id": rule_id,
                "file": file,
            }
        )

    def finding_finished(self, *, finding_id: str, status: str) -> None:
        self._append(
            {
                "ts": _now(),
                "kind": "finding_finished",
                "finding_id": finding_id,
                "status": status,
            }
        )

    def _append(self, payload: dict[str, object]) -> None:
        try:
            line = (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                descriptor = os.open(
                    self.path,
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                try:
                    os.write(descriptor, line)
                finally:
                    os.close(descriptor)
        except OSError:
            # Observability не должна ломать security pipeline
            return


def read_progress(path: str | Path, *, limit: int = 500) -> list[dict]:
    """Читаем события прогресса с учетом параллельной дозаписи"""
    file_path = Path(path)
    if not file_path.is_file():
        return []
    events: list[dict] = []
    try:
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    except OSError:
        return []
    return events[-limit:]
