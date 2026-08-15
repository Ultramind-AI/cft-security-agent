from collections.abc import Callable
from time import perf_counter

from schemas.action import ActionProposal
from schemas.execution import ExecutionResult
from schemas.validation import ValidationResult


def _safe_noop(parameters: dict) -> tuple[int, str, str]:
    message = str(parameters.get("message", "ok"))
    outcome = str(parameters.get("test_outcome", "confirmed"))

    return 0, f"safe_noop:{message}:outcome={outcome}", ""


class SafeExecutor:
    def __init__(self) -> None:
        self._registry: dict[str, Callable[[dict], tuple[int, str, str]]] = {
            "safe_noop": _safe_noop,
        }

    def execute(
        self,
        action: ActionProposal,
        validation: ValidationResult,
    ) -> ExecutionResult:
        if not validation.approved:
            return ExecutionResult(
                action_id=action.id,
                status="denied",
                stderr=validation.reason,
            )

        if validation.action_id != action.id:
            return ExecutionResult(
                action_id=action.id,
                status="denied",
                stderr="ValidationResult does not match ActionProposal",
            )

        handler = self._registry.get(action.tool)

        if handler is None:
            return ExecutionResult(
                action_id=action.id,
                status="denied",
                stderr=f"Unknown executor tool: {action.tool}",
            )

        started = perf_counter()
        exit_code, stdout, stderr = handler(action.parameters)
        duration_ms = int((perf_counter() - started) * 1000)

        return ExecutionResult(
            action_id=action.id,
            status="completed" if exit_code == 0 else "failed",
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr or None,
            duration_ms=duration_ms,
        )
