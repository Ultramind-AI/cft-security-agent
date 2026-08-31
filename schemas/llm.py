from typing import Literal

from pydantic import BaseModel, Field


class _LLMActionFields(BaseModel):
    parameters: dict[str, object] = Field(default_factory=dict)
    purpose: str = Field(min_length=1, max_length=1000)
    expected_evidence: str = Field(min_length=1, max_length=1000)


class LLMDockerfileUserActionChoice(_LLMActionFields):
    """Единственное допустимое действие живого LLM для находки о пропущенном или root USER в Dockerfile."""

    tool: Literal["inspect_dockerfile_user"]


class LLMPythonPasswordActionChoice(_LLMActionFields):
    """Единственное допустимое действие живого LLM для находки о присвоении пароля без валидации."""

    tool: Literal["inspect_python_password_assignment"]


class LLMReactHtmlFlowActionChoice(_LLMActionFields):
    """Единственное допустимое действие живого LLM для находки об опасном HTML в React."""

    tool: Literal["inspect_react_dangerous_html_flow"]


class LLMGeneralActionChoice(_LLMActionFields):
    """Текущие общие возможности без shell, доступные для рассуждений живого LLM."""

    tool: Literal["observe_http_surface"]


class LLMProbeResult(BaseModel):
    status: Literal["ready"]
    note: str = Field(min_length=1, max_length=300)


class LLMPlanStepChoice(BaseModel):
    """Один шаг reasoning layer внутри одноразовой security lab

    Registered candidates остаются для детерминированных capabilities. T14.1 разрешает
    модели предложить raw argv, но команда выполняется только в Docker sandbox
    без доступа к host и произвольной сети
    """

    kind: Literal["candidate", "sandbox_command"] = "candidate"
    candidate_id: str | None = Field(default=None, min_length=1, max_length=256)
    argv: list[str] = Field(default_factory=list, max_length=32)
    cwd: Literal["/target", "/workspace"] = "/target"
    purpose: str | None = Field(default=None, min_length=1, max_length=1000)
    expected_observation: str = Field(min_length=1, max_length=1000)
    continue_if: str = Field(min_length=1, max_length=1000)

    @classmethod
    def _argv_error(cls, values: list[str]) -> str | None:
        if any(not isinstance(value, str) or not value or "\x00" in value for value in values):
            return "sandbox command argv must contain non-empty strings without NUL bytes"
        if any(len(value) > 1024 for value in values):
            return "sandbox command argv item exceeds 1024 characters"
        if sum(len(value) for value in values) > 8192:
            return "sandbox command argv exceeds the 8192-character budget"
        return None

    def model_post_init(self, __context: object, /) -> None:
        if self.kind == "candidate":
            if self.candidate_id is None:
                raise ValueError("candidate plan step requires candidate_id")
            if self.argv:
                raise ValueError("candidate plan step cannot contain argv")
            return

        if self.candidate_id is not None:
            raise ValueError("sandbox_command plan step cannot contain candidate_id")
        if not self.argv:
            raise ValueError("sandbox_command plan step requires argv")
        if self.purpose is None:
            raise ValueError("sandbox_command plan step requires purpose")
        error = self._argv_error(self.argv)
        if error is not None:
            raise ValueError(error)


class LLMDynamicPlanChoice(BaseModel):
    """Форма DynamicPlan для provider без trusted полей identity таргета"""

    goal: str = Field(min_length=1, max_length=1000)
    max_steps: int = Field(ge=1, le=8)
    continuation_reason: str = Field(min_length=1, max_length=1000)
    stop_conditions: list[str] = Field(default_factory=list, max_length=8)
    steps: list[LLMPlanStepChoice] = Field(min_length=1, max_length=8)
