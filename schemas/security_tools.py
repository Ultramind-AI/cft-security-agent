from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DockerfileUserCheckResult(BaseModel):
    """Structured source evidence for the first Docker hardening E2E case."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["cft.dockerfile_user_check.v1"] = Field(
        validation_alias="schema",
        serialization_alias="schema",
    )
    dockerfile: Literal["backend/Dockerfile"]
    final_stage: int = Field(ge=1)
    user_directive_present: bool
    user: str | None = None
    user_line: int | None = Field(default=None, ge=1)
    verdict: Literal["confirmed", "rejected"]
    scope: Literal["source"] = "source"
    runtime_user_verified: Literal[False] = False
    explanation: str
