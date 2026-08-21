from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from evaluation.presentation import render_benchmark
from evaluation.runner import BenchmarkRunner, compare_benchmark
from schemas.evaluation import BenchmarkReport, EvaluationDataset
from schemas.pipeline import GateResult
from schemas.report import FinalReport


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate saved security-agent artifacts against a fixed dataset."
    )
    parser.add_argument("--dataset", required=True, help="Evaluation dataset YAML")
    parser.add_argument("--inputs", required=True, help="Target/report manifest YAML")
    parser.add_argument("--output", required=True, help="Benchmark JSON output")
    parser.add_argument("--text-output", help="Optional human-readable report output")
    parser.add_argument("--run-label", default="current")
    parser.add_argument("--baseline", help="Optional prior BenchmarkReport JSON")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    dataset = EvaluationDataset.model_validate(
        yaml.safe_load(Path(args.dataset).read_text(encoding="utf-8")) or {}
    )
    reports, errors = _load_inputs(Path(args.inputs))
    benchmark = BenchmarkRunner().evaluate(
        dataset=dataset,
        reports_by_target=reports,
        technical_errors_by_target=errors,
        run_label=args.run_label,
    )
    if args.baseline:
        baseline = BenchmarkReport.model_validate_json(
            Path(args.baseline).read_text(encoding="utf-8")
        )
        benchmark = compare_benchmark(benchmark, baseline)

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(benchmark.model_dump_json(indent=2) + "\n", encoding="utf-8")
    rendered = render_benchmark(benchmark)
    print(rendered)
    if args.text_output:
        text_output = Path(args.text_output).expanduser()
        text_output.parent.mkdir(parents=True, exist_ok=True)
        text_output.write_text(rendered + "\n", encoding="utf-8")
    return 0


def _load_inputs(path: Path) -> tuple[dict[str, list[FinalReport]], dict[str, int]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    targets = payload.get("targets", {})
    if not isinstance(targets, dict):
        raise TypeError("Benchmark inputs field 'targets' must be a mapping")

    reports_by_target: dict[str, list[FinalReport]] = {}
    errors_by_target: dict[str, int] = {}
    for target_id, config in targets.items():
        if not isinstance(config, dict):
            raise TypeError(f"Benchmark input for {target_id} must be a mapping")
        report_paths = config.get("reports", [])
        if not isinstance(report_paths, list):
            raise TypeError(f"Benchmark reports for {target_id} must be a list")
        reports_by_target[str(target_id)] = [
            FinalReport.model_validate_json(Path(item).read_text(encoding="utf-8"))
            for item in report_paths
        ]
        gate_path = config.get("gate")
        if gate_path:
            gate = GateResult.model_validate_json(
                Path(gate_path).read_text(encoding="utf-8")
            )
            errors_by_target[str(target_id)] = max(
                gate.technical_errors,
                len(gate.stage_errors),
            )
    return reports_by_target, errors_by_target


if __name__ == "__main__":
    raise SystemExit(main())
