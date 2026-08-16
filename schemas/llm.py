from typing import Literal

from pydantic import BaseModel, Field


class _LLMActionFields(BaseModel):
    parameters: dict[str, object] = Field(default_factory=dict)
    purpose: str = Field(min_length=1, max_length=1000)
    expected_evidence: str = Field(min_length=1, max_length=1000)


class LLMDockerfileUserActionChoice(_LLMActionFields):
    """Only valid live-LLM action for the first Dockerfile missing-USER finding."""

    tool: Literal["check_sberlab_backend_dockerfile_user"]


class LLMGeneralActionChoice(_LLMActionFields):
    """Current generic non-shell capabilities exposed to live LLM reasoning."""

    tool: Literal[
        "check_sberlab_health",
        "get_sberlab_public_projects",
    ]


class LLMProbeResult(BaseModel):
    status: Literal["ready"]
    note: str = Field(min_length=1, max_length=300)
