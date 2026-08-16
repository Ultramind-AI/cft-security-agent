from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DockerfileUserCheckResult(BaseModel):
    """Structured source evidence for a trusted Dockerfile artifact."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["cft.dockerfile_user_check.v2"] = Field(
        validation_alias="schema",
        serialization_alias="schema",
    )
    artifact_id: str = Field(min_length=1, max_length=128)
    dockerfile: str = Field(min_length=1, max_length=512)
    final_stage: int = Field(ge=1)
    user_directive_present: bool
    user: str | None = None
    user_line: int | None = Field(default=None, ge=1)
    user_classification: Literal["missing", "root", "non_root", "dynamic"]
    verdict: Literal["confirmed", "rejected", "inconclusive"]
    scope: Literal["source"] = "source"
    runtime_user_verified: Literal[False] = False
    explanation: str


class PythonPasswordAssignmentCheckResult(BaseModel):
    """Structured source evidence for Python password-assignment handling."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["cft.python_password_assignment_check.v1"] = Field(
        validation_alias="schema",
        serialization_alias="schema",
    )
    artifact_id: str = Field(min_length=1, max_length=128)
    file: str = Field(min_length=1, max_length=512)
    set_password_calls: int = Field(ge=0)
    validate_password_calls: int = Field(ge=0)
    hardcoded_password_literals: int = Field(ge=0)
    privileged_hardcoded_password_records: int = Field(ge=0)
    password_values_redacted: Literal[True] = True
    verdict: Literal["confirmed", "rejected", "inconclusive"]
    scope: Literal["source"] = "source"
    runtime_auth_verified: Literal[False] = False
    explanation: str


class ReactDangerousHtmlFlowCheckResult(BaseModel):
    """Bounded static source-flow evidence for a React dangerous HTML sink."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["cft.react_dangerous_html_flow_check.v1"] = Field(
        validation_alias="schema",
        serialization_alias="schema",
    )
    frontend_artifact_id: str = Field(min_length=1, max_length=128)
    frontend_file: str = Field(min_length=1, max_length=512)
    supporting_files: list[str] = Field(min_length=1, max_length=8)
    field: str = Field(min_length=1, max_length=64)
    dangerous_html_sink_found: bool
    sink_expression: str | None = Field(default=None, max_length=300)
    sanitizer_detected: bool
    model_field_found: bool
    serializer_field_exposed: bool | None
    serializer_field_read_only: bool | None
    model_viewset_update_route: bool
    authentication_required: bool | None
    verdict: Literal["confirmed", "rejected", "inconclusive"]
    scope: Literal["static_source_flow"] = "static_source_flow"
    browser_execution_verified: Literal[False] = False
    explanation: str
