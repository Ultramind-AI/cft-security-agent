from pydantic import BaseModel, Field

class ExecutionResult(BaseModel):
    action_id: str
    status: str
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    duration_ms: int = 0
