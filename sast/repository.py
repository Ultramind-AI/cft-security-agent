from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from schemas.finding import Finding


class JsonFindingRepository:
    """Read normalized SAST findings produced by app.sast_scan."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def list_findings(self) -> list[Finding]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Findings file not found: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Findings file is not valid JSON: {self.path}") from exc

        if not isinstance(payload, list):
            raise TypeError("Normalized findings JSON must contain a list")

        try:
            return [Finding.model_validate(item) for item in payload]
        except ValidationError as exc:
            raise ValueError(f"Invalid normalized finding in {self.path}") from exc

    def get_finding(self, finding_id: str) -> Finding:
        for finding in self.list_findings():
            if finding.id == finding_id:
                return finding
        raise KeyError(f"Finding not found: {finding_id}")

    def get_by_index(self, index: int) -> Finding:
        findings = self.list_findings()
        if index < 0 or index >= len(findings):
            raise IndexError(
                f"Finding index {index} is outside available range 0..{max(len(findings) - 1, 0)}"
            )
        return findings[index]
