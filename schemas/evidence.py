from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EvidenceVerdict = Literal["confirmed", "rejected", "inconclusive"]
EvidenceSource = Literal["static", "runtime"]
EvidenceReliability = Literal["high", "medium", "low", "unknown"]


class EvidenceAction(BaseModel):
    """Одобренное действие"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    run_id: str | None = None


class EvidenceObservation(BaseModel):
    """Факты наблюдения без вывода LLM."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1)
    facts: dict[str, object] = Field(default_factory=dict)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvidenceScope(BaseModel):
    """Ограниченная область действия наблюдения"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    service: str | None = None
    description: str = Field(min_length=1)


class EvidenceArtifact(BaseModel):
    """Постоянная ссылка для проверки наблюдения"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str = Field(min_length=1)
    role: Literal["execution", "audit", "source", "log", "other"] = "other"


class Evidence(BaseModel):
    """Неизменяемое док-во с проверяемым происхождением."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    action_id: str
    type: str
    summary: str
    artifact_refs: list[str] = Field(default_factory=list)
    reliability: EvidenceReliability = "unknown"
    verdict: EvidenceVerdict | None = None
    source: EvidenceSource
    sandbox_session_id: str | None = None
    hypothesis_id: str = Field(min_length=1)
    action: EvidenceAction
    observation: EvidenceObservation
    scope: EvidenceScope
    artifacts: list[EvidenceArtifact] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_provenance(self) -> "Evidence":
        if self.action_id != self.action.id:
            raise ValueError("Evidence action_id must match action.id")
        if self.type != self.observation.kind:
            raise ValueError("Evidence type must match observation.kind")
        artifact_refs = [artifact.ref for artifact in self.artifacts]
        if self.artifact_refs != artifact_refs:
            raise ValueError("Evidence artifact_refs must match artifacts")
        if self.source == "runtime" and not self.sandbox_session_id:
            raise ValueError("Runtime Evidence requires sandbox_session_id")
        return self
