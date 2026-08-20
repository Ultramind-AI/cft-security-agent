import json
import os
from pathlib import Path
from threading import Lock


class JsonlAuditLog:
    """Добавляет одно компактное неизменяемое событие для каждого решения Executor."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._lock = Lock()

    def append(self, event: dict) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        with self._lock:
            # Аудит отделен от потенциально большого stdout/stderr и хранит только решение
            descriptor = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, line)
            finally:
                os.close(descriptor)
        return f"audit:{event['run_id']}"

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
