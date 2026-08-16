from typing import Literal

from pydantic import BaseModel, Field

EvidenceVerdict = Literal["confirmed", "rejected", "inconclusive"]


class Evidence(BaseModel):
    id: str
    action_id: str
    type: str
    summary: str
    artifact_refs: list[str] = Field(default_factory=list)
    reliability: str = "unknown"
    verdict: EvidenceVerdict | None = None
    details: dict[str, object] = Field(default_factory=dict)
