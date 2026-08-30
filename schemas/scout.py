"""Контракт model scout, до Evidence это только кандидат."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.finding import Finding


class CandidateFinding(BaseModel):
    """Кандидат от модели с проверяемыми ссылками на уже известный код."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    file: str = Field(min_length=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    severity: str | None = None
    service: str | None = None
    provenance_paths: list[str] = Field(min_length=1, max_length=16)
    rationale: str = Field(min_length=1, max_length=2_000)
    source: str = "model_scout"

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if value != "model_scout":
            raise ValueError("CandidateFinding source must be model_scout")
        return value

    def to_finding(self, *, finding_id: str) -> Finding:
        """Дальше кандидат живет в обычном pipeline и не получает verdict."""
        return Finding(
            id=finding_id,
            source=self.source,
            rule_id=self.rule_id,
            title=self.title,
            description=self.description,
            file=self.file,
            line_start=self.line_start,
            line_end=self.line_end,
            severity=self.severity,
            service=self.service,
        )


class CandidateFindingBatch(BaseModel):
    """Ответ scout всегда остается списком непроверенных кандидатов."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[CandidateFinding] = Field(default_factory=list, max_length=32)
