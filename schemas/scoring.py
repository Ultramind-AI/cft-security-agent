from pydantic import BaseModel, Field

class CVSSResult(BaseModel):
    vector: str
    score: float
    severity: str
    reasoning: str = ""

class ContextPriority(BaseModel):
    level: str
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
