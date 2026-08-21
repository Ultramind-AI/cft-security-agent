from evaluation.presentation import render_benchmark
from evaluation.runner import BenchmarkRunner, compare_benchmark
from schemas.evaluation import EvaluationDataset
from schemas.report import FinalReport, ReportFinding, VerificationSummary
from schemas.scoring import ContextPriority


def _report(
    finding_id: str,
    *,
    status: str,
    severity: str,
    context: str,
    iterations: int,
) -> FinalReport:
    return FinalReport(
        finding_id=finding_id,
        finding=ReportFinding(
            id=finding_id,
            source="semgrep",
            rule_id="demo.rule",
            title="Demo",
            file="demo.py",
            severity=severity,
        ),
        status=status,
        verification=VerificationSummary(),
        context_priority=ContextPriority(level=context, score=5.0),
        explanation="test",
        next_step="test",
        iterations=iterations,
    )


def _dataset() -> EvaluationDataset:
    return EvaluationDataset.model_validate(
        {
            "targets": [
                {
                    "id": "one",
                    "findings": [
                        {
                            "finding_id": "a",
                            "expected_status": "confirmed",
                            "vulnerable": True,
                            "expected_severity": "ERROR",
                            "expected_context": "HIGH",
                        },
                        {
                            "finding_id": "b",
                            "expected_status": "rejected",
                            "vulnerable": False,
                        },
                    ],
                },
                {
                    "id": "two",
                    "findings": [
                        {
                            "finding_id": "c",
                            "expected_status": "inconclusive",
                            "vulnerable": False,
                        },
                        {
                            "finding_id": "d",
                            "expected_status": "confirmed",
                            "vulnerable": True,
                        },
                    ],
                },
            ]
        }
    )


def test_benchmark_metrics_are_reproducible_and_cover_two_targets() -> None:
    reports = {
        "one": [
            _report(
                "a", status="confirmed", severity="ERROR", context="HIGH", iterations=1
            ),
            _report(
                "b", status="confirmed", severity="WARNING", context="LOW", iterations=2
            ),
        ],
        "two": [
            _report(
                "c",
                status="inconclusive",
                severity="WARNING",
                context="LOW",
                iterations=3,
            )
        ],
    }
    runner = BenchmarkRunner()
    first = runner.evaluate(
        dataset=_dataset(),
        reports_by_target=reports,
        technical_errors_by_target={"two": 1},
        run_label="candidate",
    )
    second = runner.evaluate(
        dataset=_dataset(),
        reports_by_target=reports,
        technical_errors_by_target={"two": 1},
        run_label="candidate",
    )

    assert first == second
    assert first.metrics.coverage == 0.75
    assert first.metrics.confirmed == 2
    assert first.metrics.inconclusive == 1
    assert first.metrics.false_positives == 1
    assert first.metrics.technical_errors == 1
    assert first.metrics.average_agent_steps == 2.0
    assert first.metrics.precision == 0.5
    assert first.metrics.recall == 0.5
    assert first.metrics.status_accuracy == 0.6667


def test_benchmark_comparison_and_human_report() -> None:
    baseline = BenchmarkRunner().evaluate(
        dataset=_dataset(), reports_by_target={}, run_label="baseline"
    )
    current = BenchmarkRunner().evaluate(
        dataset=_dataset(), reports_by_target={}, run_label="current"
    )
    compared = compare_benchmark(current, baseline)
    rendered = render_benchmark(compared)

    assert compared.comparison_to_baseline["coverage"] == 0.0
    assert "SECURITY AGENT BENCHMARK" in rendered
    assert "comparison_to_baseline" in rendered
