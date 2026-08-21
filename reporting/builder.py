from __future__ import annotations

from pipeline.policy import classify_finding_gate
from schemas.report import (
    CIGateImpact,
    FinalReport,
    PolicyDecisionSummary,
    ReportFinding,
    SandboxActionSummary,
    VerificationSummary,
)
from schemas.state import AgentState

_ALLOWED_STATUSES = {
    "confirmed",
    "rejected",
    "inconclusive",
    "policy_blocked",
}

_EXPLANATIONS = {
    "confirmed": (
        "Capability-specific Evidence confirmed the reported security condition "
        "within the recorded evidence scope."
    ),
    "rejected": (
        "Capability-specific Evidence rejected the reported security condition "
        "within the recorded evidence scope."
    ),
    "inconclusive": (
        "The workflow ended without terminal capability-specific Evidence."
    ),
    "policy_blocked": (
        "Validator denied the proposed verification action according to policy."
    ),
}

_NEXT_STEPS = {
    "confirmed": (
        "Remediate the verified condition, then rerun SAST and the same bounded "
        "verification capability."
    ),
    "rejected": (
        "Retain the Evidence for audit and review whether the originating SAST rule "
        "needs tuning."
    ),
    "inconclusive": (
        "Collect stronger approved Evidence or add a bounded verification capability "
        "before making a terminal claim."
    ),
    "policy_blocked": (
        "Use an allowlisted verification strategy or change policy through operator review."
    ),
}

_VERIFICATION_LIMITATIONS = {
    "runtime_user_verified": "Runtime container user identity was not verified.",
    "runtime_auth_verified": "Runtime authentication behavior was not verified.",
    "browser_execution_verified": "Browser-side execution was not verified.",
}


def build_final_report(state: AgentState) -> FinalReport:
    # В отчет попадает только терминальный статус, промежуточное состояние не должно выглядеть вердиктом
    status = str(state.get("status", "inconclusive"))
    if status not in _ALLOWED_STATUSES:
        status = "inconclusive"

    finding = state["finding"]
    evidence = list(state.get("evidence", []))
    action = state.get("proposed_action")
    validation = state.get("validation")
    analysis = state.get("analysis")
    hypothesis = state.get("hypothesis")

    if validation is None:
        validator_decision = "not_run"
        validator_reason = None
    elif validation.approved:
        validator_decision = "approved"
        validator_reason = validation.reason
    else:
        validator_decision = "denied"
        validator_reason = validation.reason

    verification = VerificationSummary(
        action_id=action.id if action is not None else None,
        capability=action.tool if action is not None else None,
        target=action.target if action is not None else None,
        environment=action.environment if action is not None else None,
        validator_decision=validator_decision,
        validator_reason=validator_reason,
        evidence_count=len(evidence),
        evidence_types=list(dict.fromkeys(item.type for item in evidence)),
        decision_basis=_decision_basis(state, status),
    )
    gate = classify_finding_gate(
        finding_id=finding.id,
        status=status,
        context_level=(
            state["context_priority"].level
            if state.get("context_priority") is not None
            else None
        ),
        cvss_severity=(
            state["cvss"].severity if state.get("cvss") is not None else None
        ),
        pr_classification=(
            finding.pr_context.classification
            if finding.pr_context is not None
            else None
        ),
    )

    return FinalReport(
        finding_id=finding.id,
        finding=ReportFinding(
            id=finding.id,
            source=finding.source,
            rule_id=finding.rule_id,
            title=finding.title,
            description=finding.description,
            severity=finding.severity,
            service=finding.service,
            file=finding.file,
            line_start=finding.line_start,
            line_end=finding.line_end,
            pr_context=finding.pr_context,
        ),
        status=status,
        analysis_summary=analysis.summary if analysis is not None else None,
        risk_signals=list(analysis.risk_signals) if analysis is not None else [],
        code_context=state.get("code_context"),
        architecture_context=state.get("architecture_context"),
        hypothesis=hypothesis.statement if hypothesis is not None else None,
        hypothesis_confidence=(hypothesis.confidence if hypothesis is not None else None),
        verification=verification,
        cvss=state.get("cvss"),
        context_priority=state.get("context_priority"),
        evidence=evidence,
        sandbox_actions=_sandbox_actions(state),
        policy_decisions=_policy_decisions(state),
        ci_gate_impact=CIGateImpact(
            effect=gate.effect,
            category=gate.category,
            reason=gate.reason,
        ),
        explanation=_EXPLANATIONS[status],
        limitations=_limitations(state, status),
        next_step=_NEXT_STEPS[status],
        iterations=int(state.get("iteration_count", 0)),
    )


def _sandbox_actions(state: AgentState) -> list[SandboxActionSummary]:
    action = state.get("proposed_action")
    if action is None:
        return []

    execution = state.get("execution")
    return [
        SandboxActionSummary(
            action_id=action.id,
            capability=action.tool,
            target=action.target,
            environment=action.environment,
            purpose=action.purpose,
            parameter_names=sorted(action.parameters),
            execution_status=execution.status if execution is not None else None,
            exit_code=execution.exit_code if execution is not None else None,
            timed_out=execution.timed_out if execution is not None else False,
            artifact_refs=(list(execution.artifacts) if execution is not None else []),
        )
    ]


def _policy_decisions(state: AgentState) -> list[PolicyDecisionSummary]:
    validation = state.get("validation")
    if validation is None:
        return []
    return [
        PolicyDecisionSummary(
            action_id=validation.action_id,
            decision="approved" if validation.approved else "denied",
            reason=validation.reason,
            rules=list(validation.policy_rules),
        )
    ]


def _decision_basis(state: AgentState, status: str) -> str:
    if status == "policy_blocked":
        return "validator_policy"

    evidence = list(state.get("evidence", []))
    # Терминальный Evidence > интерпретация LLM
    if status in {"confirmed", "rejected"} and any(
        item.verdict == status for item in evidence
    ):
        return "capability_specific_evidence"

    if status == "inconclusive" and int(state.get("iteration_count", 0)) >= int(
        state.get("max_iterations", 2)
    ):
        return "iteration_limit"

    return "workflow_state"


def _limitations(state: AgentState, status: str) -> list[str]:
    limitations: list[str] = []

    for item in state.get("evidence", []):
        for key, message in _VERIFICATION_LIMITATIONS.items():
            if item.details.get(key) is False:
                limitations.append(message)

    if status == "policy_blocked":
        limitations.append("No verification action was executed after Validator denial.")
    elif status == "inconclusive":
        limitations.append("No terminal capability-specific Evidence was obtained.")

    return list(dict.fromkeys(limitations))
