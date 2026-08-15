import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from schemas.evidence import Evidence


class InMemoryEvidenceStore:
    def __init__(self) -> None:
        self._data: dict[str, Evidence] = {}

    def put(self, evidence: Evidence) -> None:
        self._data[evidence.id] = evidence

    def get(self, evidence_id: str) -> Evidence:
        try:
            return self._data[evidence_id]
        except KeyError as exc:
            raise KeyError(f"Unknown evidence id: {evidence_id}") from exc


class JsonExecutionEvidenceStore:
    """Persist executor evidence outside disposable sandbox workspaces."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).resolve()

    def put_execution(self, record: dict) -> tuple[str, str]:
        self.directory.mkdir(parents=True, exist_ok=True)
        evidence_id = f"execution-{uuid4().hex}"
        destination = self.directory / f"{evidence_id}.json"
        temporary = destination.with_suffix(".tmp")

        payload = {
            "id": evidence_id,
            "type": "executor_result",
            "created_at": datetime.now(UTC).isoformat(),
            **record,
        }
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(
                descriptor,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8"),
            )
        finally:
            os.close(descriptor)
        temporary.replace(destination)

        return evidence_id, destination.as_posix()

    def get_execution(self, evidence_id: str) -> dict:
        if re.fullmatch(r"execution-[0-9a-f]{32}", evidence_id) is None:
            raise ValueError("Invalid execution evidence id")

        path = self.directory / f"{evidence_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))
