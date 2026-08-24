from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.agent_loop import AgentDecisionRecord
from schemas.errors import ErrorDetail
from schemas.evidence import Evidence
from schemas.pipeline import GateResult
from schemas.report import FinalReport, SandboxActionSummary

ApiRunStatus = Literal["queued", "running", "completed", "technical_failure"]
ChatRole = Literal["user", "assistant", "system"]
ChatMessageKind = Literal["text", "status", "summary", "error"]


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
    analysis_request: str | None = Field(default=None, max_length=4000)


class ApiRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    target_id: str
    status: ApiRunStatus
    agent_mode: Literal["stub", "llm"] | None = None
    max_iterations: int = Field(ge=1, le=8)
    analysis_request: str | None = None
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


class CreateChatSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=160)


class SendChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    agent_mode: Literal["stub", "llm"] = "llm"
    max_iterations: int = Field(default=5, ge=1, le=8)


class ChatSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    target_id: str
    title: str
    active_run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    role: ChatRole
    kind: ChatMessageKind = "text"
    content: str
    run_id: str | None = None
    created_at: datetime


class ChatSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: ChatSession
    messages: list[ChatMessage] = Field(default_factory=list)
    run: ApiRun | None = None
    reports: list[FinalReport] = Field(default_factory=list)
    gate: GateResult | None = None


class ChatLLMAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=8000)
