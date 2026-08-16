from typing import Literal

from pydantic import BaseModel, Field

GateDecision = Literal["pass", "warn", "fail"]
FindingGateEffect = Literal["pass", "warn", "fail"]


class PipelineFindingResult(BaseModel):
    finding_id: str
    status: str
    gate_effect: FindingGateEffect
    reason: str
    report_path: str | None = None
    context_priority: str | None = None
    cvss_severity: str | None = None


class GateResult(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    decision: GateDecision
    exit_code: int = Field(ge=0, le=2)
    reports_total: int = Field(default=0, ge=0)
    confirmed: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    inconclusive: int = Field(default=0, ge=0)
    policy_blocked: int = Field(default=0, ge=0)
    reasons: list[str] = Field(default_factory=list)
    stage_errors: list[str] = Field(default_factory=list)
    findings: list[PipelineFindingResult] = Field(default_factory=list)
