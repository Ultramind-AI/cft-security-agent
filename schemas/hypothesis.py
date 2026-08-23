from uuid import uuid4

from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    """Проверяемая гипотеза с id на время одного запуска."""

    id: str = Field(default_factory=lambda: f"hypothesis-{uuid4().hex}")
    statement: str
    based_on: list[str] = Field(default_factory=list)
    expected_evidence: str
    confidence: float
