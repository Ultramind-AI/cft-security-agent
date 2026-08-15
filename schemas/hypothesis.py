from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    statement: str
    based_on: list[str] = Field(default_factory=list)
    expected_evidence: str
    confidence: float
