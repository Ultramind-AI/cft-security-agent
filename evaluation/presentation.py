from schemas.evaluation import BenchmarkReport


def render_benchmark(report: BenchmarkReport) -> str:
    metrics = report.metrics
    lines = [
        "SECURITY AGENT BENCHMARK",
        f"run={report.run_label}",
        f"dataset={report.dataset_digest}",
        (
            f"coverage={metrics.coverage:.4f} "
            f"({metrics.matched_total}/{metrics.expected_total})"
        ),
        (
            f"confirmed={metrics.confirmed} rejected={metrics.rejected} "
            f"inconclusive={metrics.inconclusive} policy_blocked={metrics.policy_blocked}"
        ),
        (
            f"false_positives={metrics.false_positives} "
            f"technical_errors={metrics.technical_errors} "
            f"average_agent_steps={metrics.average_agent_steps:.4f}"
        ),
        f"precision={_value(metrics.precision)} recall={_value(metrics.recall)}",
        f"status_accuracy={_value(metrics.status_accuracy)}",
    ]
    for target_id, target in report.targets.items():
        lines.append(
            f"target={target_id} coverage={target.coverage:.4f} "
            f"confirmed={target.confirmed} false_positives={target.false_positives} "
            f"technical_errors={target.technical_errors}"
        )
    if report.comparison_to_baseline is not None:
        lines.append("comparison_to_baseline:")
        for name, delta in report.comparison_to_baseline.items():
            lines.append(f"  {name}: {delta:+.4f}")
    return "\n".join(lines)


def _value(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"
