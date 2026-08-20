from typing import Literal

from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    run_id: str
    action_id: str
    # Статус отражает факт запуска, а не подтверждение уязвимости
    status: Literal["completed", "failed", "denied"]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    # Ссылка обязательна даже при отказе или сбое для сохранения трассировки
    evidence_ref: str
    audit_ref: str
    artifacts: list[str] = Field(default_factory=list)
    duration_ms: int = 0
