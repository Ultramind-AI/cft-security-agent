from pydantic import BaseModel, Field


class ActionProposal(BaseModel):
    # Это запрос на проверку; право на выполнение появляется только после валидатора
    id: str
    tool: str
    target: str
    environment: str = "local"
    iteration: int = Field(default=1, ge=1)
    parameters: dict = Field(default_factory=dict)
    purpose: str
    expected_evidence: str
