from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.action import ActionProposal
from schemas.execution import ExecutionResult
from schemas.validation import ValidationResult

AgentStopReason = Literal[
    "terminal_evidence",
    "policy_blocked",
    "plan_rejected",
    "step_budget_exhausted",
    "wall_clock_budget_exhausted",
    "execution_timeout",
    "build_failure",
    "unsupported_runtime",
    "isolation_or_policy_blocked",
    "execution_failed",
    "insufficient_evidence",
]


class AgentActionRecord(BaseModel):
    """Достаточно неизменяемый audit snapshot одной итерации observe/reason/act"""

    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=1)
    plan_id: str | None = None
    action: ActionProposal
    validation: ValidationResult
    execution: ExecutionResult | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentDecisionRecord(BaseModel):
    """Почему агент остановился или запросил еще одну reasoning итерацию"""

    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=0)
    outcome: Literal["continue", "stop"]
    reason: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)
    plan_id: str | None = None
    stop_reason: AgentStopReason | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
