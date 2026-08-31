"""Детерминированные правила завершения по сохраненным Evidence"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from schemas.agent_loop import AgentStopReason
from schemas.state import AgentState

GuardStatus = Literal["confirmed", "rejected", "inconclusive", "continue"]


@dataclass(frozen=True)
class EvidenceGuardDecision:
    status: GuardStatus
    explanation: str
    stop_reason: AgentStopReason | None


def evaluate_evidence(state: AgentState) -> EvidenceGuardDecision | None:
    """Возвращает одинаковое решение для одинакового состояния Evidence."""
    action = state.get("proposed_action")
    execution = state.get("execution")
    if action is None or execution is None:
        return None

    if execution.status != "completed" or execution.exit_code != 0:
        reason = _execution_reason(execution)
        if reason in {
            "build_failure",
            "unsupported_runtime",
            "isolation_or_policy_blocked",
        }:
            return EvidenceGuardDecision(
                status="inconclusive",
                explanation=_reason_explanation(reason),
                stop_reason=reason,
            )
        return _continue_or_stop(state, reason)

    terminal = {
        item.verdict
        for item in state.get("evidence", [])
        if item.action_id == action.id
        and item.verdict in {"confirmed", "rejected"}
        and item.reliability in {"high", "medium"}
    }
    if terminal == {"confirmed"}:
        return EvidenceGuardDecision(
            status="confirmed",
            explanation="Capability-specific structured Evidence confirmed the finding.",
            stop_reason="terminal_evidence",
        )
    if terminal == {"rejected"}:
        return EvidenceGuardDecision(
            status="rejected",
            explanation="Capability-specific structured Evidence rejected the finding.",
            stop_reason="terminal_evidence",
        )
    if len(terminal) > 1:
        return _continue_or_stop(state, "insufficient_evidence", conflicting=True)
    return _continue_or_stop(state, "insufficient_evidence")


def _continue_or_stop(
    state: AgentState,
    reason: AgentStopReason,
    *,
    conflicting: bool = False,
) -> EvidenceGuardDecision:
    if _step_limit_reached(state):
        return EvidenceGuardDecision(
            status="inconclusive",
            explanation=_reason_explanation(reason, conflicting=conflicting),
            stop_reason=reason,
        )
    return EvidenceGuardDecision(
        status="continue",
        explanation=_reason_explanation(reason, conflicting=conflicting),
        stop_reason=None,
    )


def _step_limit_reached(state: AgentState) -> bool:
    limit = max(1, int(state.get("max_steps", state.get("max_iterations", 2))))
    return int(state.get("iteration_count", 0)) >= limit


def _execution_reason(execution) -> AgentStopReason:
    if execution.timed_out or (
        execution.error is not None and execution.error.code == "TIMEOUT"
    ):
        return "execution_timeout"

    code = execution.error.code if execution.error is not None else None
    if code == "BUILD_FAILED":
        return "build_failure"
    if code == "UNSUPPORTED_RUNTIME":
        return "unsupported_runtime"
    if code == "ISOLATION_BLOCKED":
        return "isolation_or_policy_blocked"

    message = f"{execution.stderr} {execution.error.message if execution.error else ''}".lower()
    if execution.status == "denied" or any(
        marker in message
        for marker in ("security boundary", "isolation", "policy blocked")
    ):
        return "isolation_or_policy_blocked"
    return "execution_failed"


def _reason_explanation(
    reason: AgentStopReason,
    *,
    conflicting: bool = False,
) -> str:
    if conflicting:
        return "Structured Evidence contains conflicting terminal verdicts."
    messages = {
        "execution_timeout": "The bounded verification timed out.",
        "build_failure": "The target build failed before sufficient Evidence was collected.",
        "unsupported_runtime": "The required runtime is not supported by the approved sandbox.",
        "isolation_or_policy_blocked": "The verification was blocked by an isolation or policy boundary.",
        "execution_failed": "The approved verification did not complete successfully.",
        "insufficient_evidence": (
            "Execution completed, but capability-specific Evidence is insufficient "
            "for a terminal verdict."
        ),
    }
    return messages[reason]
