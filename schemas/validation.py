from pydantic import BaseModel, Field

class ValidationResult(BaseModel):
    approved: bool
    action_id: str
    reason: str
    policy_rules: list[str] = Field(default_factory=list)
