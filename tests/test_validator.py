from copy import deepcopy

from schemas.action import ActionProposal
from validator.validator import PolicyValidator


def _proposal(**updates) -> ActionProposal:
    data = {
        "id": "validator-test-1",
        "tool": "safe_noop",
        "target": "sberlab-local",
        "environment": "local",
        "iteration": 1,
        "parameters": {"message": "hello", "test_outcome": "confirmed"},
        "purpose": "Run a controlled verification.",
        "expected_evidence": "Structured executor evidence.",
    }
    data.update(updates)
    return ActionProposal(**data)


def _validator() -> PolicyValidator:
    return PolicyValidator.from_yaml(
        "policies/default.yaml",
        target_file="targets/sberlab.yaml",
    )


def test_validator_approves_complete_allowlisted_action() -> None:
    result = _validator().validate(_proposal())

    assert result.approved is True
    assert result.reason == "Allowed by deterministic Validator policy v0.1"
    assert "target_environment_match" in result.policy_rules
    assert "logging_required" in result.policy_rules


def test_validator_denies_unknown_target() -> None:
    result = _validator().validate(_proposal(target="unknown-target"))

    assert result.approved is False
    assert result.policy_rules[-1] == "target_denied"


def test_validator_denies_forbidden_environment() -> None:
    result = _validator().validate(_proposal(environment="production"))

    assert result.approved is False
    assert result.policy_rules[-1] == "environment_denied"


def test_validator_denies_target_environment_mismatch() -> None:
    result = _validator().validate(_proposal(environment="staging"))

    assert result.approved is False
    assert result.policy_rules[-1] == "target_environment_mismatch"


def test_validator_denies_unknown_tool() -> None:
    result = _validator().validate(_proposal(tool="unknown_tool", parameters={}))

    assert result.approved is False
    assert result.policy_rules[-1] == "tool_denied"


def test_validator_denies_iteration_over_limit() -> None:
    result = _validator().validate(_proposal(iteration=6))

    assert result.approved is False
    assert result.policy_rules[-1] == "iteration_limit_denied"


def test_validator_denies_blank_purpose() -> None:
    result = _validator().validate(_proposal(purpose="   "))

    assert result.approved is False
    assert result.policy_rules[-1] == "purpose_denied"


def test_validator_denies_blank_expected_evidence() -> None:
    result = _validator().validate(_proposal(expected_evidence=""))

    assert result.approved is False
    assert result.policy_rules[-1] == "expected_evidence_denied"


def test_validator_denies_unexpected_safe_noop_parameter() -> None:
    result = _validator().validate(
        _proposal(parameters={"message": "ok", "command": "not-allowed"})
    )

    assert result.approved is False
    assert result.policy_rules[-1] == "parameters_denied"


def test_validator_denies_invalid_enum_value() -> None:
    result = _validator().validate(
        _proposal(parameters={"test_outcome": "maybe"})
    )

    assert result.approved is False
    assert result.policy_rules[-1] == "parameter_values_denied"


def test_validator_denies_oversized_message() -> None:
    result = _validator().validate(
        _proposal(parameters={"message": "x" * 257})
    )

    assert result.approved is False
    assert result.policy_rules[-1] == "parameter_values_denied"


def test_validator_denies_http_tool_parameters() -> None:
    result = _validator().validate(
        _proposal(
            tool="observe_http_surface",
            parameters={"url": "http://example.invalid"},
        )
    )

    assert result.approved is False
    assert result.policy_rules[-1] == "parameters_denied"


def test_validator_denies_when_logging_is_not_mandatory() -> None:
    validator = _validator()
    policy = deepcopy(validator.policy)
    policy["logging"]["required"] = False

    result = PolicyValidator(
        policy,
        target_environments=validator.target_environments,
    ).validate(_proposal())

    assert result.approved is False
    assert result.policy_rules[-1] == "logging_required_denied"


def test_validator_denies_unsupported_policy_version() -> None:
    validator = _validator()
    policy = deepcopy(validator.policy)
    policy["version"] = 999

    result = PolicyValidator(policy).validate(_proposal())

    assert result.approved is False
    assert result.policy_rules[-1] == "policy_version_denied"


def test_validator_allows_registered_target_profile() -> None:
    from schemas.target import TargetProfile

    profile = TargetProfile.model_validate(
        {
            "id": "second-local",
            "environment": "sandbox",
            "runtime": {"base_url": "http://127.0.0.1:9100"},
        }
    )
    proposal = _proposal(target=profile.id, environment=profile.environment)

    result = PolicyValidator.from_yaml(
        "policies/default.yaml",
        target_profile=profile,
    ).validate(proposal)

    assert result.approved is True


def test_validator_rejects_unregistered_artifact() -> None:
    from schemas.target import TargetProfile

    profile = TargetProfile.model_validate(
        {
            "id": "second-local",
            "environment": "sandbox",
            "runtime": {"base_url": "http://127.0.0.1:9100"},
            "artifacts": {
                "known": {"kind": "dockerfile", "path": "Dockerfile"},
            },
        }
    )
    proposal = _proposal(
        target=profile.id,
        environment=profile.environment,
        tool="inspect_dockerfile_user",
        parameters={"artifact_id": "unknown"},
    )

    result = PolicyValidator.from_yaml(
        "policies/default.yaml",
        target_profile=profile,
    ).validate(proposal)

    assert result.approved is False
    assert result.policy_rules[-1] == "target_artifact_denied"


def test_validator_allows_bounded_sandbox_command_contract() -> None:
    result = _validator().validate(
        _proposal(
            tool="sandbox_command",
            parameters={"argv": ["python", "-V"], "cwd": "/target"},
        )
    )

    assert result.approved is True
    assert "parameter_values_valid" in result.policy_rules


def test_validator_rejects_invalid_sandbox_command_argv() -> None:
    result = _validator().validate(
        _proposal(
            tool="sandbox_command",
            parameters={"argv": [""], "cwd": "/target"},
        )
    )

    assert result.approved is False
    assert result.policy_rules[-1] == "parameter_values_denied"
