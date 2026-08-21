from __future__ import annotations

import hashlib
from collections import Counter

from schemas.evaluation import (
    BenchmarkMetrics,
    BenchmarkReport,
    EvaluationDataset,
    EvaluationTarget,
)
from schemas.report import FinalReport


class BenchmarkRunner:
    def evaluate(
        self,
        *,
        dataset: EvaluationDataset,
        reports_by_target: dict[str, list[FinalReport]],
        technical_errors_by_target: dict[str, int] | None = None,
        run_label: str = "current",
    ) -> BenchmarkReport:
        errors = technical_errors_by_target or {}
        target_metrics: dict[str, BenchmarkMetrics] = {}

        for target in dataset.targets:
            target_metrics[target.id] = _target_metrics(
                target,
                reports_by_target.get(target.id, []),
                technical_errors=errors.get(target.id, 0),
            )

        return BenchmarkReport(
            run_label=run_label,
            dataset_digest=_dataset_digest(dataset),
            metrics=_aggregate_metrics(target_metrics.values()),
            targets=target_metrics,
        )


def compare_benchmark(
    current: BenchmarkReport,
    baseline: BenchmarkReport,
) -> BenchmarkReport:
    if current.dataset_digest != baseline.dataset_digest:
        raise ValueError("Cannot compare benchmark reports from different datasets")
    fields = (
        "coverage",
        "confirmed",
        "false_positives",
        "technical_errors",
        "average_agent_steps",
        "status_accuracy",
        "severity_accuracy",
        "context_accuracy",
        "precision",
        "recall",
    )
    deltas: dict[str, float] = {}
    for field in fields:
        current_value = getattr(current.metrics, field)
        baseline_value = getattr(baseline.metrics, field)
        if current_value is not None and baseline_value is not None:
            deltas[field] = round(float(current_value) - float(baseline_value), 4)
    return current.model_copy(update={"comparison_to_baseline": deltas})


def _target_metrics(
    target: EvaluationTarget,
    reports: list[FinalReport],
    *,
    technical_errors: int,
) -> BenchmarkMetrics:
    reports_by_id = {report.finding_id: report for report in reports}
    reports_by_fingerprint = {
        report.finding.pr_context.fingerprint: report
        for report in reports
        if report.finding.pr_context is not None
    }
    matched: list[tuple] = []
    for expected in target.findings:
        report = reports_by_id.get(expected.finding_id)
        if report is None and expected.fingerprint is not None:
            report = reports_by_fingerprint.get(expected.fingerprint)
        if report is not None:
            matched.append((expected, report))

    counts = Counter(report.status for report in reports)
    true_positives = sum(
        1
        for expected, report in matched
        if expected.vulnerable and report.status == "confirmed"
    )
    false_positives = sum(
        1
        for expected, report in matched
        if not expected.vulnerable and report.status == "confirmed"
    )
    known_positives = sum(1 for expected in target.findings if expected.vulnerable)
    status_matches = sum(
        1 for expected, report in matched if expected.expected_status == report.status
    )
    total_steps = sum(report.iterations for report in reports)
    severity_expected = [
        (expected, report)
        for expected, report in matched
        if expected.expected_severity is not None
    ]
    context_expected = [
        (expected, report)
        for expected, report in matched
        if expected.expected_context is not None
    ]
    severity_matches = sum(
        1
        for expected, report in severity_expected
        if report.finding.severity is not None
        and report.finding.severity.upper() == expected.expected_severity.upper()
    )
    context_matches = sum(
        1
        for expected, report in context_expected
        if report.context_priority is not None
        and report.context_priority.level.upper() == expected.expected_context.upper()
    )

    return BenchmarkMetrics(
        expected_total=len(target.findings),
        observed_total=len(reports),
        matched_total=len(matched),
        coverage=_ratio(len(matched), len(target.findings)),
        confirmed=counts.get("confirmed", 0),
        rejected=counts.get("rejected", 0),
        inconclusive=counts.get("inconclusive", 0),
        policy_blocked=counts.get("policy_blocked", 0),
        true_positives=true_positives,
        false_positives=false_positives,
        ground_truth_positives=known_positives,
        technical_errors=technical_errors,
        average_agent_steps=_ratio(total_steps, len(reports)),
        status_matches=status_matches,
        severity_expected_total=len(severity_expected),
        severity_matches=severity_matches,
        context_expected_total=len(context_expected),
        context_matches=context_matches,
        status_accuracy=(
            _ratio(status_matches, len(matched)) if matched else None
        ),
        severity_accuracy=(
            _ratio(
                severity_matches,
                len(severity_expected),
            )
            if severity_expected
            else None
        ),
        context_accuracy=(
            _ratio(
                context_matches,
                len(context_expected),
            )
            if context_expected
            else None
        ),
        precision=(
            _ratio(true_positives, true_positives + false_positives)
            if true_positives + false_positives
            else None
        ),
        recall=(
            _ratio(true_positives, known_positives) if known_positives else None
        ),
    )


def _aggregate_metrics(metrics_items) -> BenchmarkMetrics:
    items = list(metrics_items)
    expected = sum(item.expected_total for item in items)
    observed = sum(item.observed_total for item in items)
    matched = sum(item.matched_total for item in items)
    confirmed = sum(item.confirmed for item in items)
    false_positives = sum(item.false_positives for item in items)
    true_positives = sum(item.true_positives for item in items)
    ground_truth_positives = sum(item.ground_truth_positives for item in items)
    total_steps = sum(item.average_agent_steps * item.observed_total for item in items)
    status_matches = sum(item.status_matches for item in items)
    severity_expected = sum(item.severity_expected_total for item in items)
    severity_matches = sum(item.severity_matches for item in items)
    context_expected = sum(item.context_expected_total for item in items)
    context_matches = sum(item.context_matches for item in items)

    return BenchmarkMetrics(
        expected_total=expected,
        observed_total=observed,
        matched_total=matched,
        coverage=_ratio(matched, expected),
        confirmed=confirmed,
        rejected=sum(item.rejected for item in items),
        inconclusive=sum(item.inconclusive for item in items),
        policy_blocked=sum(item.policy_blocked for item in items),
        true_positives=true_positives,
        false_positives=false_positives,
        ground_truth_positives=ground_truth_positives,
        technical_errors=sum(item.technical_errors for item in items),
        average_agent_steps=_ratio(total_steps, observed),
        status_matches=status_matches,
        severity_expected_total=severity_expected,
        severity_matches=severity_matches,
        context_expected_total=context_expected,
        context_matches=context_matches,
        status_accuracy=_ratio(status_matches, matched) if matched else None,
        severity_accuracy=(
            _ratio(severity_matches, severity_expected) if severity_expected else None
        ),
        context_accuracy=(
            _ratio(context_matches, context_expected) if context_expected else None
        ),
        precision=(
            _ratio(true_positives, true_positives + false_positives)
            if true_positives + false_positives
            else None
        ),
        recall=(
            _ratio(true_positives, ground_truth_positives)
            if ground_truth_positives
            else None
        ),
    )


def _dataset_digest(dataset: EvaluationDataset) -> str:
    payload = dataset.model_dump_json(exclude_none=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _ratio(numerator: float, denominator: int) -> float:
    return round(float(numerator) / denominator, 4) if denominator else 0.0
