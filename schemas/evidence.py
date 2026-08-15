from pydantic import BaseModel, Field


class Evidence(BaseModel):
    id: str
    action_id: str
    type: str
    summary: str
    artifact_refs: list[str] = Field(default_factory=list)
    reliability: str = "unknown"
