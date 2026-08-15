from pathlib import Path

import yaml

from schemas.action import ActionProposal
from schemas.validation import ValidationResult


class PolicyValidator:
    def __init__(self, policy: dict):
        self.policy = policy

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PolicyValidator":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(data or {})

    def validate(self, action: ActionProposal) -> ValidationResult:
        allowed_targets = set(self.policy.get("targets", {}).get("allowed", []))
        allowed_tools = set(self.policy.get("tools", {}).get("allowed", []))

        rules: list[str] = []

        if action.target not in allowed_targets:
            return ValidationResult(
                approved=False,
                action_id=action.id,
                reason=f"Target '{action.target}' is not allowed",
                policy_rules=["target_denied"],
            )

        rules.append("target_allowed")

        if action.tool not in allowed_tools:
            return ValidationResult(
                approved=False,
                action_id=action.id,
                reason=f"Tool '{action.tool}' is not allowed",
                policy_rules=rules + ["tool_denied"],
            )

        rules.append("tool_allowed")

        return ValidationResult(
            approved=True,
            action_id=action.id,
            reason="Allowed by deterministic starter policy",
            policy_rules=rules,
        )
