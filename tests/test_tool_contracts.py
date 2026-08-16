import pytest

from schemas.action import ActionProposal
from tools.contracts import (
    TOOL_CONTRACTS,
    ToolAccess,
    ToolPermission,
    describe_tool_contracts,
    get_tool_contract,
)

EXPECTED_TOOL_NAMES = {
    "read_finding",
    "read_code_context",
    "get_architecture_context",
    "calculate_cvss",
    "calculate_context_priority",
    "request_verification",
    "read_evidence",
}


def test_catalog_contains_expected_v01_tools() -> None:
    assert {contract.name for contract in TOOL_CONTRACTS} == EXPECTED_TOOL_NAMES
    assert len(TOOL_CONTRACTS) == len(EXPECTED_TOOL_NAMES)


def test_every_contract_has_machine_readable_metadata() -> None:
    for contract in TOOL_CONTRACTS:
        description = contract.describe()

        assert contract.purpose.strip()
        assert contract.permissions
        assert contract.errors
        assert description["input_schema"]["type"] == "object"
        assert description["output_schema"]["type"] == "object"


def test_only_verification_request_requires_validator() -> None:
    gated = [contract for contract in TOOL_CONTRACTS if contract.validator_required]

    assert [contract.name for contract in gated] == ["request_verification"]
    assert gated[0].access is ToolAccess.EXECUTION_REQUEST
    assert gated[0].permissions == (ToolPermission.VERIFICATION_REQUEST,)


def test_catalog_does_not_grant_direct_execution_permission() -> None:
    permissions = {
        permission.value
        for contract in TOOL_CONTRACTS
        for permission in contract.permissions
    }

    assert "execution:direct" not in permissions


def test_verification_contract_preserves_action_proposal_schema() -> None:
    contract = get_tool_contract("request_verification")
    schema = contract.input_model.model_json_schema()

    action_schema = schema["$defs"]["ActionProposal"]
    required = set(action_schema["required"])

    assert {
        "id",
        "tool",
        "target",
        "purpose",
        "expected_evidence",
    } <= required

    sample = contract.input_model(
        action=ActionProposal(
            id="action-tool-contract-test",
            tool="safe_noop",
            target="sberlab-local",
            environment="local",
            parameters={"message": "contract test"},
            purpose="Verify contract wiring only.",
            expected_evidence="Structured safe_noop result.",
        )
    )
    assert sample.action.tool == "safe_noop"


def test_cvss_contract_requires_explicit_metrics_field() -> None:
    contract = get_tool_contract("calculate_cvss")
    schema = contract.input_model.model_json_schema()

    assert "metrics" in schema["required"]
    assert "must not invent missing metrics" in schema["properties"]["metrics"][
        "description"
    ]


def test_unknown_contract_name_fails_closed() -> None:
    with pytest.raises(KeyError, match="Unknown tool contract"):
        get_tool_contract("arbitrary_shell")


def test_catalog_is_json_serializable_shape() -> None:
    descriptions = describe_tool_contracts()

    assert len(descriptions) == 7
    assert all("input_schema" in item for item in descriptions)
    assert all("output_schema" in item for item in descriptions)
