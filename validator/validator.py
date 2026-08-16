import re
from pathlib import Path
from typing import Any

import yaml

from schemas.action import ActionProposal
from schemas.validation import ValidationResult

_ACTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SUPPORTED_POLICY_VERSIONS = {1}


class PolicyValidator:
    """Deterministic policy gate between Agent and Executor."""

    def __init__(
        self,
        policy: dict[str, Any],
        *,
        target_environments: dict[str, str] | None = None,
    ) -> None:
        self.policy = policy
        self.target_environments = dict(target_environments or {})

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        target_file: str | Path | None = None,
    ) -> "PolicyValidator":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        target_environments: dict[str, str] = {}

        if target_file is not None:
            target_data = (
                yaml.safe_load(Path(target_file).read_text(encoding="utf-8")) or {}
            )
            target_id = target_data.get("id")
            environment = target_data.get("environment")
            if target_id and environment:
                target_environments[str(target_id)] = str(environment)

        return cls(data, target_environments=target_environments)

    def validate(self, action: ActionProposal) -> ValidationResult:
        rules: list[str] = []

        version = self.policy.get("version")
        if version not in _SUPPORTED_POLICY_VERSIONS:
            return self._deny(
                action,
                "Unsupported or missing policy version",
                rules,
                "policy_version_denied",
            )
        rules.append("policy_version_supported")

        if not _ACTION_ID_RE.fullmatch(action.id):
            return self._deny(
                action,
                "Action id must be 1-128 safe identifier characters",
                rules,
                "action_id_denied",
            )
        rules.append("action_id_valid")

        allowed_targets = set(self.policy.get("targets", {}).get("allowed", []))
        if action.target not in allowed_targets:
            return self._deny(
                action,
                f"Target '{action.target}' is not allowed",
                rules,
                "target_denied",
            )
        rules.append("target_allowed")

        allowed_environments = set(
            self.policy.get("environments", {}).get("allowed", [])
        )
        if action.environment not in allowed_environments:
            return self._deny(
                action,
                f"Environment '{action.environment}' is not allowed",
                rules,
                "environment_denied",
            )
        rules.append("environment_allowed")

        expected_environment = self.target_environments.get(action.target)
        if (
            expected_environment is not None
            and action.environment != expected_environment
        ):
            return self._deny(
                action,
                (
                    f"Target '{action.target}' belongs to environment "
                    f"'{expected_environment}', not '{action.environment}'"
                ),
                rules,
                "target_environment_mismatch",
            )
        if expected_environment is not None:
            rules.append("target_environment_match")

        allowed_tools = set(self.policy.get("tools", {}).get("allowed", []))
        if action.tool not in allowed_tools:
            return self._deny(
                action,
                f"Tool '{action.tool}' is not allowed",
                rules,
                "tool_denied",
            )
        rules.append("tool_allowed")

        max_iterations = int(self.policy.get("limits", {}).get("max_iterations", 0))
        if max_iterations < 1 or action.iteration > max_iterations:
            return self._deny(
                action,
                (
                    f"Iteration {action.iteration} exceeds policy limit "
                    f"{max_iterations}"
                ),
                rules,
                "iteration_limit_denied",
            )
        rules.append("iteration_within_limit")

        if not action.purpose.strip():
            return self._deny(
                action,
                "Action purpose is required",
                rules,
                "purpose_denied",
            )
        rules.append("purpose_present")

        if not action.expected_evidence.strip():
            return self._deny(
                action,
                "Expected evidence is required",
                rules,
                "expected_evidence_denied",
            )
        rules.append("expected_evidence_present")

        parameter_error = self._validate_parameters(action)
        if parameter_error is not None:
            reason, rule = parameter_error
            return self._deny(action, reason, rules, rule)
        rules.extend(
            [
                "parameters_allowed",
                "parameter_values_valid",
            ]
        )

        if self.policy.get("logging", {}).get("required") is not True:
            return self._deny(
                action,
                "Approval requires mandatory audit logging",
                rules,
                "logging_required_denied",
            )
        rules.append("logging_required")

        return ValidationResult(
            approved=True,
            action_id=action.id,
            reason="Allowed by deterministic Validator policy v0.1",
            policy_rules=rules,
        )

    def _validate_parameters(
        self,
        action: ActionProposal,
    ) -> tuple[str, str] | None:
        contracts = self.policy.get("tools", {}).get("parameter_contracts", {})
        contract = contracts.get(action.tool)
        if not isinstance(contract, dict):
            return (
                f"No parameter contract is defined for tool '{action.tool}'",
                "parameter_contract_denied",
            )

        allowed_keys = set(contract.get("allowed_keys", []))
        required_keys = set(contract.get("required_keys", []))
        actual_keys = set(action.parameters)

        unexpected = sorted(actual_keys - allowed_keys)
        if unexpected:
            return (
                f"Unsupported parameters for '{action.tool}': {unexpected}",
                "parameters_denied",
            )

        missing = sorted(required_keys - actual_keys)
        if missing:
            return (
                f"Missing required parameters for '{action.tool}': {missing}",
                "parameters_denied",
            )

        enum_values = contract.get("enum_values", {})
        for name, allowed_values in enum_values.items():
            if name in action.parameters and action.parameters[name] not in allowed_values:
                return (
                    f"Invalid value for parameter '{name}'",
                    "parameter_values_denied",
                )

        max_string_lengths = contract.get("max_string_lengths", {})
        for name, max_length in max_string_lengths.items():
            if name not in action.parameters:
                continue
            value = action.parameters[name]
            if not isinstance(value, str):
                return (
                    f"Parameter '{name}' must be a string",
                    "parameter_values_denied",
                )
            if len(value) > int(max_length):
                return (
                    f"Parameter '{name}' exceeds maximum length {max_length}",
                    "parameter_values_denied",
                )

        return None

    @staticmethod
    def _deny(
        action: ActionProposal,
        reason: str,
        passed_rules: list[str],
        denied_rule: str,
    ) -> ValidationResult:
        return ValidationResult(
            approved=False,
            action_id=action.id,
            reason=reason,
            policy_rules=[*passed_rules, denied_rule],
        )
