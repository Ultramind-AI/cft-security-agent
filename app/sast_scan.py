from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sast.semgrep_runner import SemgrepError, run_semgrep_scan


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Semgrep SAST against a local target repository and normalize findings."
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Path to the local repository to scan, for example ../sberlab_hack",
    )
    parser.add_argument(
        "--config",
        default="auto",
        help="Semgrep config/ruleset. MVP default: auto",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/sast",
        help="Directory for raw and normalized reports",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="Maximum wall time for the local SAST process",
    )
    return parser.parse_args()


def _summary(findings: list, semgrep_errors: list) -> dict:
    by_severity = Counter((finding.severity or "UNKNOWN").upper() for finding in findings)
    by_service = Counter(finding.service or "unknown" for finding in findings)
    return {
        "total": len(findings),
        "by_severity": dict(sorted(by_severity.items())),
        "by_service": dict(sorted(by_service.items())),
        "semgrep_errors": len(semgrep_errors),
    }


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = run_semgrep_scan(
            args.target,
            config=args.config,
            timeout_seconds=args.timeout_seconds,
        )
    except SemgrepError as exc:
        print(f"SAST scan failed: {exc}")
        return 2

    raw_path = output_dir / "semgrep.json"
    findings_path = output_dir / "findings.json"
    summary_path = output_dir / "summary.json"

    raw_path.write_text(
        json.dumps(result.raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    findings_path.write_text(
        json.dumps(
            [finding.model_dump(mode="json") for finding in result.findings],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    semgrep_errors = result.raw.get("errors", [])
    if not isinstance(semgrep_errors, list):
        semgrep_errors = []
    summary = _summary(result.findings, semgrep_errors)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"SAST target: {result.target}")
    print(f"Semgrep config: {result.config}")
    print(f"Findings: {summary['total']}")
    print(f"By severity: {summary['by_severity']}")
    print(f"By service: {summary['by_service']}")
    print(f"Semgrep parse/runtime errors in report: {summary['semgrep_errors']}")
    print(f"Raw report: {raw_path}")
    print(f"Normalized findings: {findings_path}")
    print(f"Summary: {summary_path}")

    if result.findings:
        print("\nFirst findings for manual triage:")
        for finding in result.findings[:10]:
            severity = finding.severity or "UNKNOWN"
            location = f"{finding.file}:{finding.line_start or '?'}"
            print(f"- [{severity}] {finding.rule_id} | {location}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
