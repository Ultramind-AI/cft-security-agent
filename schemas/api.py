from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.agent_loop import AgentDecisionRecord
from schemas.errors import ErrorDetail
from schemas.evidence import Evidence
from schemas.report import SandboxActionSummary

ApiRunStatus = Literal["queued", "running", "completed", "technical_failure"]


class ApiProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    environment: str
    services: list[str] = Field(default_factory=list)
    repository_available: bool


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1, max_length=128)
    agent_mode: Literal["stub", "llm"] | None = None
    max_iterations: int = Field(default=3, ge=1, le=8)


class ApiRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    target_id: str
    status: ApiRunStatus
    agent_mode: Literal["stub", "llm"] | None = None
    max_iterations: int = Field(ge=1, le=8)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = Field(default=None, ge=0, le=2)
    gate_decision: Literal["pass", "warn", "fail"] | None = None
    error: ErrorDetail | None = None


class ApiFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    finding_id: str
    status: str
    source: str
    rule_id: str
    title: str
    severity: str | None = None
    service: str | None = None
    file: str
    line_start: int | None = None
    report_available: bool = True


class ApiEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    finding_id: str
    evidence: Evidence


class FindingTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    agent_decisions: list[AgentDecisionRecord] = Field(default_factory=list)
    sandbox_actions: list[SandboxActionSummary] = Field(default_factory=list)


class RunTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    findings: list[FindingTimeline] = Field(default_factory=list)
