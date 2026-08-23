from __future__ import annotations

from evidence.presentation import format_evidence_scope
from schemas.report import FinalReport

_STATUS_LABELS = {
    "confirmed": "CONFIRMED",
    "rejected": "REJECTED",
    "inconclusive": "INCONCLUSIVE",
    "policy_blocked": "POLICY BLOCKED",
}

_BASIS_LABELS = {
    "capability_specific_evidence": "capability-specific Evidence",
    "validator_policy": "Validator policy",
    "iteration_limit": "iteration limit",
    "workflow_state": "workflow state",
}


def render_final_report(report: FinalReport) -> str:
    """Отобразить стабильную схему FinalReport в компактном удобном для демо текстовом отчете."""
    finding = report.finding
    line = finding.line_start if finding.line_start is not None else "?"
    severity = finding.severity or "unknown"
    service = finding.service or "unknown"
    verification = report.verification

    lines = [
        "=" * 72,
        "FINAL SECURITY REPORT",
        "=" * 72,
        f"Status: {_STATUS_LABELS[report.status]}",
        "",
        "Finding",
        f"  Title: {finding.title}",
        f"  Rule: {finding.rule_id}",
        f"  Location: {finding.file}:{line}",
        f"  Service: {service}",
        f"  SAST severity: {severity}",
    ]
    if finding.description:
        lines.append(f"  Source description: {finding.description}")
    if finding.pr_context is not None:
        pr_context = finding.pr_context
        lines.extend(
            [
                "",
                "Pull Request",
                f"  Range: {pr_context.base_ref}...{pr_context.head_ref}",
                f"  Fingerprint: {pr_context.fingerprint}",
                f"  Classification: {pr_context.classification}",
                f"  Changed lines: {pr_context.changed_lines or 'none'}",
                f"  Architecture changed: {'yes' if pr_context.architecture_context_changed else 'no'}",
            ]
        )

    lines.extend(["", "Context"])
    if report.code_context:
        lines.append("  Code context:")
        lines.extend(f"    {line}" for line in report.code_context.splitlines())
    else:
        lines.append("  Code context: missing")
    if report.architecture_context is None:
        lines.append("  Architecture context: missing")
    else:
        architecture = report.architecture_context
        lines.append(
            "  Architecture: "
            f"public={architecture.public_exposure}, "
            f"criticality={architecture.criticality}, "
            f"auth={architecture.authentication}, "
            f"blast_radius={architecture.blast_radius}"
        )
        lines.append(
            "  Connections: "
            + (", ".join(architecture.connected_services) or "none")
        )

    if report.analysis_summary is not None:
        lines.extend(["", "Agent assessment", f"  Analysis: {report.analysis_summary}"])
        if report.hypothesis is not None:
            confidence = (
                "unknown"
                if report.hypothesis_confidence is None
                else f"{report.hypothesis_confidence:.2f}"
            )
            lines.append(f"  Hypothesis: {report.hypothesis} (confidence={confidence})")

    decision = verification.validator_decision.upper().replace("_", " ")
    lines.extend(
        [
            "",
            "Verification",
            f"  Capability: {verification.capability or 'none'}",
            f"  Validator: {decision}",
            f"  Decision basis: {_BASIS_LABELS[verification.decision_basis]}",
            f"  Iterations: {report.iterations}",
        ]
    )
    if verification.validator_reason:
        lines.append(f"  Validator reason: {verification.validator_reason}")

    lines.extend(["", f"Sandbox actions ({len(report.sandbox_actions)})"])
    if not report.sandbox_actions:
        lines.append("  No sandbox action proposed.")
    else:
        for action in report.sandbox_actions:
            lines.append(
                f"  {action.action_id}: {action.capability} → {action.target} "
                f"[{action.execution_status or 'not executed'}]"
            )

    lines.extend(["", f"Policy decisions ({len(report.policy_decisions)})"])
    if not report.policy_decisions:
        lines.append("  Validator was not run.")
    else:
        for decision_item in report.policy_decisions:
            lines.append(
                f"  {decision_item.decision.upper()}: {decision_item.reason}"
            )
            if decision_item.rules:
                lines.append(f"    Rules: {', '.join(decision_item.rules)}")

    lines.extend(["", f"Evidence ({len(report.evidence)})"])
    if not report.evidence:
        lines.append("  No Evidence collected.")
    else:
        for index, item in enumerate(report.evidence, start=1):
            verdict = (item.verdict or "none").upper()
            lines.append(
                f"  [{index}] {verdict} | {item.reliability} | {item.type}"
            )
            lines.append(f"      {item.summary}")
            scope = format_evidence_scope(
                item.scope.description,
                item.observation.facts,
            )
            if scope is not None:
                lines.append(f"      {scope}")

    lines.extend(["", "Risk"])
    if report.cvss is None:
        lines.append("  CVSS: missing")
    else:
        score = "N/A" if report.cvss.score is None else f"{report.cvss.score:.1f}"
        lines.append(
            f"  CVSS: {report.cvss.severity} (score={score}, vector={report.cvss.vector})"
        )

    if report.context_priority is None:
        lines.append("  Context priority: missing")
    else:
        score = (
            "N/A"
            if report.context_priority.score is None
            else f"{report.context_priority.score:.1f}"
        )
        lines.append(
            f"  Context priority: {report.context_priority.level} (score={score})"
        )
        for reason in report.context_priority.reasons:
            lines.append(f"    - {reason}")

    lines.extend(["", "Conclusion", f"  {report.explanation}"])

    if report.ci_gate_impact is not None:
        lines.extend(
            [
                "",
                "CI Gate impact",
                f"  Effect: {report.ci_gate_impact.effect.upper()}",
                f"  Category: {report.ci_gate_impact.category}",
                f"  Reason: {report.ci_gate_impact.reason}",
            ]
        )

    if report.limitations:
        lines.extend(["", "Limitations"])
        for limitation in report.limitations:
            lines.append(f"  - {limitation}")

    lines.extend(["", "Next step", f"  {report.next_step}", "=" * 72])
    return "\n".join(lines)
