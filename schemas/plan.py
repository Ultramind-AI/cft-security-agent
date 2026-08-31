from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.action import ActionProposal


class PlannedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1, le=8)
    action: ActionProposal
    expected_observation: str = Field(min_length=1, max_length=1000)
    continue_if: str = Field(min_length=1, max_length=1000)


class DynamicPlan(BaseModel):
    """Структурированный план проверки от reasoning layer

    План содержит только данные и не дает разрешение на запуск
    Каждое действие все равно проходит детерминированный Validator перед выполнением
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cft.dynamic_plan.v1"] = "cft.dynamic_plan.v1"
    id: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=64)
    hypothesis_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=1000)
    max_steps: int = Field(ge=1, le=8)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    continuation_reason: str = Field(min_length=1, max_length=1000)
    stop_conditions: list[str] = Field(default_factory=list, max_length=8)
    steps: list[PlannedAction] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_internal_consistency(self) -> DynamicPlan:
        if len(self.steps) > self.max_steps:
            raise ValueError("DynamicPlan steps cannot exceed max_steps")

        expected_indexes = list(range(1, len(self.steps) + 1))
        if [step.index for step in self.steps] != expected_indexes:
            raise ValueError("DynamicPlan step indexes must be sequential from 1")

        action_ids = [step.action.id for step in self.steps]
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("DynamicPlan action ids must be unique")

        for step in self.steps:
            if step.action.target != self.target:
                raise ValueError("DynamicPlan action target must match plan target")
            if step.action.environment != self.environment:
                raise ValueError("DynamicPlan action environment must match plan environment")
        return self


class PlanValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    plan_id: str
    reason: str
    rules: list[str] = Field(default_factory=list)
