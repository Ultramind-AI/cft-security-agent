from __future__ import annotations

from datetime import UTC, datetime

from schemas.agent_loop import AgentDecisionRecord, AgentStopReason
from schemas.agent_outputs import ReevaluationResult
from schemas.state import AgentState


def current_step_limit(state: AgentState) -> int:
    return max(
        1,
        int(state.get("max_steps", state.get("max_iterations", 2))),
    )


def wall_clock_exhausted(state: AgentState, *, now: datetime | None = None) -> bool:
    started_at = state.get("started_at")
    if started_at is None:
        return False
    budget = float(state.get("wall_clock_budget_seconds", 120.0))
    if budget <= 0:
        return True
    current = now or datetime.now(UTC)
    return (current - started_at).total_seconds() >= budget


def budget_stop_reason(state: AgentState) -> AgentStopReason | None:
    if int(state.get("iteration_count", 0)) >= current_step_limit(state):
        return "step_budget_exhausted"
    if wall_clock_exhausted(state):
        return "wall_clock_budget_exhausted"
    return None


def terminal_evidence_status(state: AgentState) -> str | None:
    from evidence.guard import evaluate_evidence

    decision = evaluate_evidence(state)
    if decision is not None and decision.status in {"confirmed", "rejected"}:
        return decision.status
    return None


def apply_budget_to_reevaluation(
    state: AgentState,
    result: ReevaluationResult,
) -> tuple[ReevaluationResult, AgentStopReason | None]:
    if result.status in {"confirmed", "rejected"}:
        return result, "terminal_evidence"

    reason = budget_stop_reason(state)
    if result.status == "inconclusive" and reason is not None:
        return result, reason
    if result.status == "inconclusive":
        execution = state.get("execution")
        if execution is not None and execution.status != "completed":
            return result, "execution_failed"
        return result, "insufficient_evidence"

    if reason == "step_budget_exhausted":
        return (
            ReevaluationResult(
                status="inconclusive",
                explanation="Agent step budget was exhausted without terminal Evidence.",
            ),
            reason,
        )
    if reason == "wall_clock_budget_exhausted":
        return (
            ReevaluationResult(
                status="inconclusive",
                explanation="Agent wall-clock budget was exhausted without terminal Evidence.",
            ),
            reason,
        )
    return result, None


def decision_record(
    state: AgentState,
    result: ReevaluationResult,
    stop_reason: AgentStopReason | None,
) -> AgentDecisionRecord:
    action = state.get("proposed_action")
    action_id = action.id if action is not None else None
    evidence_ids = [
        item.id
        for item in state.get("evidence", [])
        if action_id is None or item.action_id == action_id
    ]
    plan = state.get("dynamic_plan")
    return AgentDecisionRecord(
        step=int(state.get("iteration_count", 0)),
        outcome="continue" if result.status == "continue" else "stop",
        reason=result.explanation,
        evidence_ids=evidence_ids,
        plan_id=plan.id if plan is not None else None,
        stop_reason=stop_reason,
    )
