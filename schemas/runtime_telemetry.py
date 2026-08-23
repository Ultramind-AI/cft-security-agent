from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

RuntimeTelemetryKind = Literal[
    "build_log",
    "start_log",
    "runtime_log",
    "runtime_error",
    "container_state",
    "listening_port",
    "process_exit",
    "collection_error",
]
RuntimeTelemetrySource = Literal[
    "sandbox_manager",
    "docker_logs",
    "docker_events",
    "docker_inspect",
    "container_procfs",
    "collector",
]
RuntimeTelemetryLevel = Literal["info", "warning", "error"]


class RuntimeTelemetryEvent(BaseModel):
    """Одно проверяемое событие из управляемой target-сессии."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"telemetry-{uuid4().hex}")
    sequence: int = Field(default=0, ge=0)
    session_id: str = Field(min_length=1)
    run_id: str | None = None
    target: str = Field(min_length=1)
    service: str | None = None
    container_id: str | None = None
    kind: RuntimeTelemetryKind
    source: RuntimeTelemetrySource
    level: RuntimeTelemetryLevel = "info"
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    facts: dict[str, object] = Field(default_factory=dict)


class RuntimeTelemetryTimeline(BaseModel):
    """События одного запуска, упорядоченные внутри sandbox-сессии."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    run_id: str | None = None
    target: str = Field(min_length=1)
    events: list[RuntimeTelemetryEvent] = Field(default_factory=list)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_event_links(self) -> RuntimeTelemetryTimeline:
        sequences = [event.sequence for event in self.events]
        if sequences != list(range(len(self.events))):
            raise ValueError("Telemetry event sequence must be continuous and ordered")
        for event in self.events:
            if event.session_id != self.session_id:
                raise ValueError("Telemetry event session_id must match timeline")
            if event.run_id != self.run_id:
                raise ValueError("Telemetry event run_id must match timeline")
            if event.target != self.target:
                raise ValueError("Telemetry event target must match timeline")
        return self
