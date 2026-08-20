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

    tool: Literal[
        "check_sberlab_health",
        "get_sberlab_public_projects",
    ]


class LLMProbeResult(BaseModel):
    status: Literal["ready"]
    note: str = Field(min_length=1, max_length=300)
