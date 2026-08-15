from pydantic import BaseModel, Field


class ActionProposal(BaseModel):
    id: str
    tool: str
    target: str
    parameters: dict = Field(default_factory=dict)
    purpose: str
    expected_evidence: str
