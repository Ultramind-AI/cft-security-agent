from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from schemas.execution import ExecutionResult


class SandboxActionResult(BaseModel):
    session_id: str | None = None
    runtime_instance_id: str | None = None
    run_id: str
    action_id: str
    tool: str
    service: str | None = None
    status: Literal["completed", "failed", "denied"]
    stdout: str = ""
    stderr: str = ""
    exit_code: int
    timed_out: bool = False
    duration_ms: int = 0
    evidence_ref: str
    execution: ExecutionResult


class SandboxRunResult(BaseModel):
    run_id: str
    runtime_instance_id: str | None = None
    status: Literal["completed", "failed", "denied"]
    results: list[SandboxActionResult] = Field(default_factory=list)
