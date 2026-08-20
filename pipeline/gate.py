from __future__ import annotations

from collections import Counter

from schemas.pipeline import GateResult, PipelineFindingResult
from schemas.report import FinalReport

_DECISION_RANK = {"pass": 0, "warn": 1, "fail": 2}
_FAIL_CVSS_SEVERITIES = {"HIGH", "CRITICAL"}
_FAIL_CONTEXT_LEVELS = {"HIGH", "CRITICAL"}


def evaluate_gate(
    reports: list[FinalReport],
    *,
    stage_errors: list[str] | None = None,
    report_paths: dict[str, str] | None = None,
) -> GateResult:
    """Преобразовать итоговые отчеты в одно детерминированное решение CI/CD.

    Политика v1:
    - FAIL: обязательный этап завершился с ошибкой или подтвержденная находка имеет
      HIGH/CRITICAL по CVSS или контекстному приоритету
    - WARN: подтвержденная находка с более низким приоритетом, неопределенный результат
      или блокировка политики
    - PASS: предупреждений и блокирующих условий нет (например, находок нет или все
      проверенные находки отклонены)

    WARN намеренно завершается с кодом 0, чтобы пайплайн мог остаться неблокирующим и
    при этом показать результаты для проверки. FAIL завершается с кодом 1. Внутренние
    ошибки этапов используют код 2.
    """

    errors = list(stage_errors or [])
    paths = report_paths or {}
    finding_results = [
        _classify_report(report, report_path=paths.get(report.finding_id)) for report in reports
    ]

    # Ошибка пайплайна и риск финдинга - разные причины отказа, но обе блокируют CI
    if errors:
        decision = "fail"
        exit_code = 2
    else:
        decision = max(
            (item.gate_effect for item in finding_results),
            key=lambda item: _DECISION_RANK[item],
            default="pass",
        )
        exit_code = 1 if decision == "fail" else 0

    counts = Counter(report.status for report in reports)
    reasons = list(
        dict.fromkeys(
            item.reason for item in finding_results if item.gate_effect != "pass"
        )
    )
    if errors:
        reasons.insert(0, "A mandatory pipeline stage did not complete successfully.")

    return GateResult(
        decision=decision,
        exit_code=exit_code,
        reports_total=len(reports),
        confirmed=counts.get("confirmed", 0),
        rejected=counts.get("rejected", 0),
        inconclusive=counts.get("inconclusive", 0),
        policy_blocked=counts.get("policy_blocked", 0),
        reasons=reasons,
        stage_errors=errors,
        findings=finding_results,
    )


def _classify_report(report: FinalReport, *, report_path: str | None) -> PipelineFindingResult:
    context_level = report.context_priority.level.upper() if report.context_priority else None
    cvss_severity = report.cvss.severity.upper() if report.cvss else None

    if report.status == "rejected":
        effect = "pass"
        reason = f"{report.finding_id}: capability-specific Evidence rejected the finding."
    elif report.status == "inconclusive":
        effect = "warn"
        reason = f"{report.finding_id}: verification is inconclusive and needs review."
    elif report.status == "policy_blocked":
        effect = "warn"
        reason = f"{report.finding_id}: Validator policy blocked verification."
    elif report.status == "confirmed" and (
        context_level in _FAIL_CONTEXT_LEVELS or cvss_severity in _FAIL_CVSS_SEVERITIES
    ):
        effect = "fail"
        risk_basis = _risk_basis(context_level=context_level, cvss_severity=cvss_severity)
        reason = f"{report.finding_id}: confirmed with {risk_basis}."
    else:
        effect = "warn"
        reason = f"{report.finding_id}: confirmed, but below the blocking HIGH/CRITICAL threshold."

    return PipelineFindingResult(
        finding_id=report.finding_id,
        status=report.status,
        gate_effect=effect,
        reason=reason,
        report_path=report_path,
        context_priority=context_level,
        cvss_severity=cvss_severity,
    )


def _risk_basis(*, context_level: str | None, cvss_severity: str | None) -> str:
    parts: list[str] = []
    if context_level in _FAIL_CONTEXT_LEVELS:
        parts.append(f"context priority {context_level}")
    if cvss_severity in _FAIL_CVSS_SEVERITIES:
        parts.append(f"CVSS severity {cvss_severity}")
    return " and ".join(parts)
