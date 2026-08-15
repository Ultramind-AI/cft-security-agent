import json
from dataclasses import dataclass
from hashlib import sha256

from schemas.action import ActionProposal
from schemas.validation import ValidationResult


def proposal_digest(action: ActionProposal) -> str:
    """Return a stable digest that binds approval to the complete proposal."""
    payload = action.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ApprovalRecord:
    action_id: str
    proposal_digest: str
    reason: str
    policy_rules: tuple[str, ...]


class InMemoryApprovalStore:
    """Trusted in-process approval store for the executor prototype."""

    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}

    def record(
        self,
        action: ActionProposal,
        validation: ValidationResult,
    ) -> ApprovalRecord:
        if not validation.approved:
            raise ValueError("Denied ValidationResult cannot create an approval")

        if validation.action_id != action.id:
            raise ValueError("ValidationResult does not match ActionProposal")

        record = ApprovalRecord(
            action_id=action.id,
            proposal_digest=proposal_digest(action),
            reason=validation.reason,
            policy_rules=tuple(validation.policy_rules),
        )
        self._records[action.id] = record
        return record

    def check(self, action: ActionProposal) -> tuple[bool, str]:
        record = self._records.get(action.id)

        if record is None:
            return False, "No trusted approval exists for this action"

        if record.proposal_digest != proposal_digest(action):
            return False, "ActionProposal changed after approval"

        return True, "Approved proposal digest matches"
