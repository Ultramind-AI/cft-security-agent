from typing import Literal

from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    run_id: str
    action_id: str
    status: Literal["completed", "failed", "denied"]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    evidence_ref: str
    audit_ref: str
    artifacts: list[str] = Field(default_factory=list)
    duration_ms: int = 0
