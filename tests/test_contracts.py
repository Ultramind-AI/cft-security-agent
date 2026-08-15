from schemas.action import ActionProposal
from validator.validator import PolicyValidator
from executor.executor import SafeExecutor

def test_allowed_noop_roundtrip() -> None:
    proposal = ActionProposal(
        id="a1",
        tool="safe_noop",
        target="sberlab-local",
        parameters={"message": "hello"},
        purpose="contract test",
        expected_evidence="successful structured no-op",
    )
    validation = PolicyValidator.from_yaml("policies/default.yaml").validate(proposal)
    assert validation.approved is True
    result = SafeExecutor().execute(proposal, validation)
    assert result.status == "completed"
    assert result.exit_code == 0

def test_unknown_tool_is_denied() -> None:
    proposal = ActionProposal(
        id="a2",
        tool="unknown_tool",
        target="sberlab-local",
        parameters={},
        purpose="negative test",
        expected_evidence="denial",
    )
    validation = PolicyValidator.from_yaml("policies/default.yaml").validate(proposal)
    assert validation.approved is False
