from evidence.store import JsonExecutionEvidenceStore
from executor.approvals import InMemoryApprovalStore
from executor.executor import SafeExecutor
from schemas.action import ActionProposal
from validator.validator import PolicyValidator


def test_allowed_noop_roundtrip(tmp_path) -> None:
    proposal = ActionProposal(
        id="a1",
        tool="safe_noop",
        target="sberlab-local",
        parameters={"message": "hello"},
        purpose="contract test",
        expected_evidence="successful structured no-op",
    )

    validator = PolicyValidator.from_yaml("policies/default.yaml")
    validation = validator.validate(proposal)

    assert validation.approved is True

    approvals = InMemoryApprovalStore()
    approvals.record(proposal, validation)
    result = SafeExecutor.from_config(
        approvals=approvals,
        policy_file="policies/default.yaml",
        target_file="targets/sberlab.yaml",
        evidence_directory=tmp_path / "evidence",
        audit_log_path=tmp_path / "audit.jsonl",
        workspace_directory=tmp_path / "workspaces",
        target_base_url="http://127.0.0.1:8000",
    ).execute(
        proposal,
    )

    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.evidence_ref.startswith("execution-")
    record = JsonExecutionEvidenceStore(tmp_path / "evidence").get_execution(
        result.evidence_ref
    )
    assert record["limits"]["wall_time_seconds"] == 5
    assert record["limits"]["memory_bytes"] == 256 * 1024 * 1024
    assert (tmp_path / "audit.jsonl").is_file()


def test_unknown_tool_is_denied() -> None:
    proposal = ActionProposal(
        id="a2",
        tool="unknown_tool",
        target="sberlab-local",
        parameters={},
        purpose="negative test",
        expected_evidence="denial",
    )

    validator = PolicyValidator.from_yaml("policies/default.yaml")
    validation = validator.validate(proposal)

    assert validation.approved is False
