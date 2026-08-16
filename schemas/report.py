from typing import Literal

from pydantic import BaseModel, Field

from schemas.evidence import Evidence
from schemas.scoring import ContextPriority, CVSSResult

ReportStatus = Literal["confirmed", "rejected", "inconclusive", "policy_blocked"]
DecisionBasis = Literal[
    "capability_specific_evidence",
    "validator_policy",
    "iteration_limit",
    "workflow_state",
]
ValidatorDecision = Literal["approved", "denied", "not_run"]


class ReportFinding(BaseModel):
    id: str
    source: str
    rule_id: str
    title: str
    severity: str | None = None
    service: str | None = None
    file: str
    line_start: int | None = None
    line_end: int | None = None


class VerificationSummary(BaseModel):
    action_id: str | None = None
    capability: str | None = None
    target: str | None = None
    environment: str | None = None
    validator_decision: ValidatorDecision = "not_run"
    validator_reason: str | None = None
    evidence_count: int = Field(default=0, ge=0)
    evidence_types: list[str] = Field(default_factory=list)
    decision_basis: DecisionBasis = "workflow_state"


class FinalReport(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    finding_id: str
    finding: ReportFinding
    status: ReportStatus

    analysis_summary: str | None = None
    risk_signals: list[str] = Field(default_factory=list)
    hypothesis: str | None = None
    hypothesis_confidence: float | None = None

    verification: VerificationSummary
    cvss: CVSSResult | None = None
    context_priority: ContextPriority | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    explanation: str
    limitations: list[str] = Field(default_factory=list)
    next_step: str
    iterations: int = 0
