from typing import Literal

from pydantic import BaseModel, Field


class _LLMActionFields(BaseModel):
    parameters: dict[str, object] = Field(default_factory=dict)
    purpose: str = Field(min_length=1, max_length=1000)
    expected_evidence: str = Field(min_length=1, max_length=1000)


class LLMDockerfileUserActionChoice(_LLMActionFields):
    """Only valid live-LLM action for a Dockerfile missing/root USER finding."""

    tool: Literal["inspect_dockerfile_user"]


class LLMPythonPasswordActionChoice(_LLMActionFields):
    """Only valid live-LLM action for the unvalidated password-assignment finding."""

    tool: Literal["inspect_python_password_assignment"]


class LLMReactHtmlFlowActionChoice(_LLMActionFields):
    """Only valid live-LLM action for the React dangerous HTML finding."""

    tool: Literal["inspect_react_dangerous_html_flow"]


class LLMGeneralActionChoice(_LLMActionFields):
    """Current generic non-shell capabilities exposed to live LLM reasoning."""

    tool: Literal[
        "check_sberlab_health",
        "get_sberlab_public_projects",
    ]


class LLMProbeResult(BaseModel):
    status: Literal["ready"]
    note: str = Field(min_length=1, max_length=300)
