from __future__ import annotations

from collections import Counter

from pipeline.policy import classify_finding_gate
from schemas.errors import ErrorDetail
from schemas.pipeline import GateResult, PipelineFindingResult
from schemas.report import FinalReport

_DECISION_RANK = {"pass": 0, "warn": 1, "fail": 2}


def evaluate_gate(
    reports: list[FinalReport],
    *,
    errors: list[ErrorDetail] | None = None,
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

    structured_errors = list(errors or [])
    structured_errors.extend(
        ErrorDetail(
            code="INTERNAL_ERROR",
            layer="pipeline",
            message=message,
        )
        for message in (stage_errors or [])
    )
    paths = report_paths or {}
    finding_results = [
        _classify_report(report, report_path=paths.get(report.finding_id)) for report in reports
    ]

    # Ошибка пайплайна и риск финдинга - разные причины отказа, но обе блокируют CI
    if structured_errors:
        decision = "fail"
        exit_code = 2
        decision_basis = "technical_pipeline_error"
    else:
        decision = max(
            (item.gate_effect for item in finding_results),
            key=lambda item: _DECISION_RANK[item],
            default="pass",
        )
        exit_code = 1 if decision == "fail" else 0
        if decision == "fail":
            decision_basis = "confirmed_risk"
        elif decision == "warn":
            decision_basis = "policy_or_uncertainty"
        else:
            decision_basis = "no_blocking_condition"

    counts = Counter(report.status for report in reports)
    reasons = list(
        dict.fromkeys(
            item.reason for item in finding_results if item.gate_effect != "pass"
        )
    )
    if structured_errors:
        reasons.insert(0, "A mandatory pipeline stage did not complete successfully.")

    return GateResult(
        decision=decision,
        exit_code=exit_code,
        decision_basis=decision_basis,
        reports_total=len(reports),
        confirmed=counts.get("confirmed", 0),
        rejected=counts.get("rejected", 0),
        inconclusive=counts.get("inconclusive", 0),
        policy_blocked=counts.get("policy_blocked", 0),
        technical_errors=len(structured_errors),
        reasons=reasons,
        errors=structured_errors,
        findings=finding_results,
    )


def _classify_report(report: FinalReport, *, report_path: str | None) -> PipelineFindingResult:
    context_level = report.context_priority.level.upper() if report.context_priority else None
    cvss_severity = report.cvss.severity.upper() if report.cvss else None
    classification = classify_finding_gate(
        finding_id=report.finding_id,
        status=report.status,
        context_level=context_level,
        cvss_severity=cvss_severity,
        pr_classification=(
            report.finding.pr_context.classification
            if report.finding.pr_context is not None
            else None
        ),
    )

    return PipelineFindingResult(
        finding_id=report.finding_id,
        status=report.status,
        gate_effect=classification.effect,
        category=classification.category,
        reason=classification.reason,
        report_path=report_path,
        context_priority=context_level,
        cvss_severity=cvss_severity,
    )
