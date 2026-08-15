from typing import Literal
from pydantic import BaseModel, Field
from schemas.evidence import Evidence
from schemas.scoring import CVSSResult, ContextPriority

class FinalReport(BaseModel):
    finding_id: str
    status: Literal["confirmed", "rejected", "inconclusive", "policy_blocked"]
    cvss: CVSSResult | None = None
    context_priority: ContextPriority | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    explanation: str
    iterations: int = 0
