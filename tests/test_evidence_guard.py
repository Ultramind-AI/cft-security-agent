from __future__ import annotations

import pytest

from evidence.guard import evaluate_evidence
from schemas.action import ActionProposal
from schemas.errors import ErrorDetail
from schemas.evidence import Evidence, EvidenceAction, EvidenceObservation, EvidenceScope
from schemas.execution import ExecutionResult


def _action() -> ActionProposal:
    return ActionProposal(
        id="action-guard",
        tool="observe_http_surface",
        target="target-local",
        environment="sandbox",
        service="api",
        endpoint="/health/",
        purpose="Collect bounded runtime Evidence.",
        expected_evidence="Structured observation.",
    )


def _execution(
    *,
    status: str = "completed",
    exit_code: int = 0,
    timed_out: bool = False,
    error: ErrorDetail | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-guard",
        action_id="action-guard",
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
        stderr=error.message if error is not None else "",
        evidence_ref="execution-guard",
        audit_ref="audit:run-guard",
        error=error,
    )


def _evidence(
    *,
    verdict: str | None,
    source: str = "static",
    reliability: str = "high",
    action_id: str = "action-guard",
) -> Evidence:
    return Evidence(
        id=f"evidence-{source}-{verdict or 'none'}-{reliability}-{action_id}",
        action_id=action_id,
        type="guard_observation",
        summary="Synthetic structured Evidence.",
        reliability=reliability,
        verdict=verdict,
        source=source,
        sandbox_session_id="session-guard" if source == "runtime" else None,
        hypothesis_id="hypothesis-guard",
        action=EvidenceAction(id=action_id, tool="observe_http_surface"),
        observation=EvidenceObservation(kind="guard_observation", facts={}),
        scope=EvidenceScope(
            target="target-local",
            environment="sandbox",
            description="guard fixture",
        ),
    )


def _state(*, evidence: list[Evidence], execution: ExecutionResult | None = None, step: int = 1, limit: int = 2) -> dict:
    return {
        "proposed_action": _action(),
        "execution": execution or _execution(),
        "evidence": evidence,
        "iteration_count": step,
        "max_steps": limit,
    }


def test_static_evidence_confirms_deterministically() -> None:
    decision = evaluate_evidence(_state(evidence=[_evidence(verdict="confirmed")]))

    assert decision is not None
    assert (decision.status, decision.stop_reason) == ("confirmed", "terminal_evidence")


def test_runtime_evidence_rejects_deterministically() -> None:
    decision = evaluate_evidence(
        _state(evidence=[_evidence(verdict="rejected", source="runtime")])
    )

    assert decision is not None
    assert (decision.status, decision.stop_reason) == ("rejected", "terminal_evidence")


def test_mixed_evidence_order_does_not_change_terminal_result() -> None:
    static = _evidence(verdict="confirmed")
    runtime = _evidence(verdict="confirmed", source="runtime")

    first = evaluate_evidence(_state(evidence=[static, runtime]))
    second = evaluate_evidence(_state(evidence=[runtime, static]))

    assert first == second
    assert first is not None and first.status == "confirmed"


@pytest.mark.parametrize(
    ("step", "expected_status", "expected_reason"),
    [(1, "continue", None), (2, "inconclusive", "insufficient_evidence")],
)
def test_conflicting_terminal_evidence_requires_more_evidence_or_stops_at_limit(
    step: int,
    expected_status: str,
    expected_reason: str | None,
) -> None:
    decision = evaluate_evidence(
        _state(
            evidence=[
                _evidence(verdict="confirmed"),
                _evidence(verdict="rejected", source="runtime"),
            ],
            step=step,
        )
    )

    assert decision is not None
    assert (decision.status, decision.stop_reason) == (expected_status, expected_reason)


def test_low_reliability_terminal_evidence_is_insufficient() -> None:
    decision = evaluate_evidence(
        _state(evidence=[_evidence(verdict="confirmed", reliability="low")], step=2)
    )

    assert decision is not None
    assert (decision.status, decision.stop_reason) == (
        "inconclusive",
        "insufficient_evidence",
    )


@pytest.mark.parametrize(
    ("step", "expected_status", "expected_reason"),
    [(1, "continue", None), (2, "inconclusive", "execution_timeout")],
)
def test_timeout_is_retryable_only_until_the_step_limit(
    step: int,
    expected_status: str,
    expected_reason: str | None,
) -> None:
    decision = evaluate_evidence(
        _state(
            evidence=[],
            execution=_execution(status="failed", exit_code=124, timed_out=True),
            step=step,
        )
    )

    assert decision is not None
    assert (decision.status, decision.stop_reason) == (expected_status, expected_reason)


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("BUILD_FAILED", "build_failure"),
        ("UNSUPPORTED_RUNTIME", "unsupported_runtime"),
        ("ISOLATION_BLOCKED", "isolation_or_policy_blocked"),
    ],
)
def test_typed_technical_failures_have_stable_inconclusive_reasons(
    code: str,
    reason: str,
) -> None:
    decision = evaluate_evidence(
        _state(
            evidence=[],
            execution=_execution(
                status="failed",
                exit_code=1,
                error=ErrorDetail(code=code, layer="executor", message="controlled"),
            ),
        )
    )

    assert decision is not None
    assert (decision.status, decision.stop_reason) == ("inconclusive", reason)


def test_denied_execution_is_an_isolation_or_policy_block() -> None:
    decision = evaluate_evidence(
        _state(evidence=[], execution=_execution(status="denied", exit_code=126))
    )

    assert decision is not None
    assert (decision.status, decision.stop_reason) == (
        "inconclusive",
        "isolation_or_policy_blocked",
    )


def test_evidence_from_another_action_cannot_end_the_current_action() -> None:
    decision = evaluate_evidence(
        _state(evidence=[_evidence(verdict="confirmed", action_id="other-action")], step=2)
    )

    assert decision is not None
    assert (decision.status, decision.stop_reason) == (
        "inconclusive",
        "insufficient_evidence",
    )
