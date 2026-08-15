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
