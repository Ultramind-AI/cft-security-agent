from typing import Literal

from pydantic import BaseModel, Field


class AnalysisResult(BaseModel):
    summary: str
    risk_signals: list[str] = Field(default_factory=list)
    needs_verification: bool = True


class ReevaluationResult(BaseModel):
    status: Literal["confirmed", "rejected", "inconclusive", "continue"]
    explanation: str
