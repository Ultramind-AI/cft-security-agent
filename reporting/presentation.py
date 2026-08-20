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
            scope = format_evidence_scope(item.details)
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

    if report.limitations:
        lines.extend(["", "Limitations"])
        for limitation in report.limitations:
            lines.append(f"  - {limitation}")

    lines.extend(["", "Next step", f"  {report.next_step}", "=" * 72])
    return "\n".join(lines)
