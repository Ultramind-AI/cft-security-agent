from __future__ import annotations

from typing import NamedTuple

from schemas.pipeline import FindingGateEffect, GateCategory

_FAIL_CVSS_SEVERITIES = {"HIGH", "CRITICAL"}
_FAIL_CONTEXT_LEVELS = {"HIGH", "CRITICAL"}


class FindingGateClassification(NamedTuple):
    effect: FindingGateEffect
    category: GateCategory
    reason: str


def classify_finding_gate(
    *,
    finding_id: str,
    status: str,
    context_level: str | None,
    cvss_severity: str | None,
    pr_classification: str | None = None,
) -> FindingGateClassification:
    """Return the deterministic per-finding CI effect used by reports and gate."""
    context = context_level.upper() if context_level else None
    cvss = cvss_severity.upper() if cvss_severity else None

    if status == "rejected":
        return FindingGateClassification(
            "pass",
            "none",
            f"{finding_id}: capability-specific Evidence rejected the finding.",
        )
    if status == "inconclusive":
        return FindingGateClassification(
            "warn",
            "inconclusive",
            f"{finding_id}: verification is inconclusive and needs review.",
        )
    if status == "policy_blocked":
        return FindingGateClassification(
            "warn",
            "policy_block",
            f"{finding_id}: Validator policy blocked verification.",
        )
    if status == "confirmed" and (
        context in _FAIL_CONTEXT_LEVELS or cvss in _FAIL_CVSS_SEVERITIES
    ):
        risk_basis = _risk_basis(context_level=context, cvss_severity=cvss)
        if pr_classification == "existing":
            return FindingGateClassification(
                "warn",
                "confirmed_risk",
                f"{finding_id}: pre-existing confirmed {risk_basis}; PR policy warns.",
            )
        return FindingGateClassification(
            "fail",
            "confirmed_risk",
            f"{finding_id}: confirmed with {risk_basis}"
            + (
                f" and classified as {pr_classification}."
                if pr_classification is not None
                else "."
            ),
        )
    if status == "confirmed":
        return FindingGateClassification(
            "warn",
            "confirmed_risk",
            f"{finding_id}: confirmed, but below the blocking HIGH/CRITICAL threshold.",
        )
    return FindingGateClassification(
        "warn",
        "inconclusive",
        f"{finding_id}: unsupported report status needs review.",
    )


def _risk_basis(*, context_level: str | None, cvss_severity: str | None) -> str:
    parts: list[str] = []
    if context_level in _FAIL_CONTEXT_LEVELS:
        parts.append(f"context priority {context_level}")
    if cvss_severity in _FAIL_CVSS_SEVERITIES:
        parts.append(f"CVSS severity {cvss_severity}")
    return " and ".join(parts)
