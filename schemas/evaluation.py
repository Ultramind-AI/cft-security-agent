from typing import Literal

from pydantic import BaseModel, Field


class ExpectedFinding(BaseModel):
    finding_id: str
    fingerprint: str | None = None
    expected_status: Literal[
        "confirmed", "rejected", "inconclusive", "policy_blocked"
    ]
    vulnerable: bool
    expected_severity: str | None = None
    expected_context: str | None = None


class EvaluationTarget(BaseModel):
    id: str
    findings: list[ExpectedFinding] = Field(default_factory=list)


class EvaluationDataset(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    targets: list[EvaluationTarget] = Field(min_length=2)


class BenchmarkMetrics(BaseModel):
    expected_total: int = Field(ge=0)
    observed_total: int = Field(ge=0)
    matched_total: int = Field(ge=0)
    coverage: float = Field(ge=0.0, le=1.0)
    confirmed: int = Field(ge=0)
    rejected: int = Field(ge=0)
    inconclusive: int = Field(ge=0)
    policy_blocked: int = Field(ge=0)
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    ground_truth_positives: int = Field(ge=0)
    technical_errors: int = Field(ge=0)
    average_agent_steps: float = Field(ge=0.0)
    status_matches: int = Field(ge=0)
    severity_expected_total: int = Field(ge=0)
    severity_matches: int = Field(ge=0)
    context_expected_total: int = Field(ge=0)
    context_matches: int = Field(ge=0)
    status_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    severity_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    context_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    precision: float | None = Field(default=None, ge=0.0, le=1.0)
    recall: float | None = Field(default=None, ge=0.0, le=1.0)


class BenchmarkReport(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    run_label: str
    dataset_digest: str
    metrics: BenchmarkMetrics
    targets: dict[str, BenchmarkMetrics] = Field(default_factory=dict)
    comparison_to_baseline: dict[str, float] | None = None
