from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.agent_loop import AgentDecisionRecord
from schemas.errors import ErrorDetail
from schemas.evidence import Evidence
from schemas.pipeline import GateResult
from schemas.report import FinalReport, SandboxActionSummary

ApiRunStatus = Literal[
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "completed",
    "technical_failure",
]
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
    exit_code: int | None = Field(default=None, ge=0, le=255)
    gate_decision: Literal["pass", "warn", "fail"] | None = None
    cancellation_reason: str | None = None
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


class RunStageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(max_length=64)
    status: str = Field(max_length=32)
    detail: str | None = Field(default=None, max_length=500)
    at: str | None = None


class RunActivityEvent(BaseModel):
    """Одно выполненное sandbox действие из audit log Executor"""

    model_config = ConfigDict(extra="forbid")

    action_id: str
    tool: str
    target: str | None = None
    status: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    at: str | None = None


class RunFindingProgressEvent(BaseModel):
    """Очищенное событие жизненного цикла finding из progress journal"""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(max_length=256)
    status: Literal["started", "finished"]
    title: str | None = Field(default=None, max_length=300)
    severity: str | None = Field(default=None, max_length=32)
    rule_id: str | None = Field(default=None, max_length=300)
    file: str | None = Field(default=None, max_length=1024)
    index: int | None = Field(default=None, ge=1)
    total: int | None = Field(default=None, ge=1)
    result: str | None = Field(default=None, max_length=32)
    at: str | None = None


class RunProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[RunStageEvent] = Field(default_factory=list)
    activities: list[RunActivityEvent] = Field(default_factory=list)
    finding_events: list[RunFindingProgressEvent] = Field(default_factory=list)
    findings_total: int | None = None
    findings_done: int = 0
    current_finding: str | None = None


class DiscoveryComponentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    root: str
    technologies: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    dockerfiles: list[str] = Field(default_factory=list)
    local_addresses: list[str] = Field(default_factory=list)


class RunDiscoveryView(BaseModel):
    """Очищенные факты Discovery без локальных путей репозитория"""

    model_config = ConfigDict(extra="forbid")

    components: list[DiscoveryComponentView] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChatRunSnapshot(BaseModel):
    """Один запуск и его очищенные presentation artifacts внутри чата"""

    model_config = ConfigDict(extra="forbid")

    run: ApiRun
    reports: list[FinalReport] = Field(default_factory=list)
    gate: GateResult | None = None
    progress: RunProgress | None = None
    discovery: RunDiscoveryView | None = None


class ChatSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: ChatSession
    messages: list[ChatMessage] = Field(default_factory=list)
    run: ApiRun | None = None
    reports: list[FinalReport] = Field(default_factory=list)
    gate: GateResult | None = None
    progress: RunProgress | None = None
    discovery: RunDiscoveryView | None = None
    runs: list[ChatRunSnapshot] = Field(default_factory=list)


class ImportedProjectFile(BaseModel):
    """Один relative path проекта из browser folder picker"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1024)
    # Пустые файлы вроде __init__.py кодируются пустой Base64 строкой и остаются валидными
    content_base64: str = Field(max_length=200_000_000)


class ImportProjectFilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    files: list[ImportedProjectFile] = Field(min_length=1, max_length=10_000)


class ChatLLMAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=8000)
