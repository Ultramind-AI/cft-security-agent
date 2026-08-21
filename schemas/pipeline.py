from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from schemas.errors import ErrorDetail

GateDecision = Literal["pass", "warn", "fail"]
FindingGateEffect = Literal["pass", "warn", "fail"]
GateCategory = Literal[
    "none",
    "confirmed_risk",
    "policy_block",
    "inconclusive",
    "technical_pipeline_error",
]
GateDecisionBasis = Literal[
    "no_blocking_condition",
    "confirmed_risk",
    "policy_or_uncertainty",
    "technical_pipeline_error",
]


class PipelineFindingResult(BaseModel):
    finding_id: str
    status: str
    gate_effect: FindingGateEffect
    category: GateCategory = "none"
    reason: str
    report_path: str | None = None
    context_priority: str | None = None
    cvss_severity: str | None = None


class GateResult(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    decision: GateDecision
    exit_code: int = Field(ge=0, le=2)
    decision_basis: GateDecisionBasis = "no_blocking_condition"
    reports_total: int = Field(default=0, ge=0)
    confirmed: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    inconclusive: int = Field(default=0, ge=0)
    policy_blocked: int = Field(default=0, ge=0)
    technical_errors: int = Field(default=0, ge=0)
    reasons: list[str] = Field(default_factory=list)
    errors: list[ErrorDetail] = Field(default_factory=list)
    stage_errors: list[str] = Field(default_factory=list)
    findings: list[PipelineFindingResult] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_stage_errors(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        raw_errors = list(normalized.get("errors") or [])
        legacy_messages = list(normalized.get("stage_errors") or [])

        if raw_errors:
            errors = [ErrorDetail.model_validate(error) for error in raw_errors]
        else:
            errors = [
                ErrorDetail(
                    code="INTERNAL_ERROR",
                    layer="pipeline",
                    message=message,
                )
                for message in legacy_messages
            ]

        normalized["errors"] = errors
        normalized["stage_errors"] = [error.message for error in errors]
        return normalized
