from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from agent.graph import build_graph
from app.config import settings
from app.e2e_inputs import build_real_initial_state
from pipeline.gate import evaluate_gate
from reporting.presentation import render_final_report
from sast.repository import JsonFindingRepository
from sast.semgrep_runner import SemgrepError, run_semgrep_scan
from schemas.pipeline import GateResult
from schemas.report import FinalReport


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run SAST, verify every normalized finding with the bounded agent workflow, "
            "write FinalReport JSON artifacts, and return a deterministic CI/CD gate."
        )
    )
    parser.add_argument("--target", default="../sberlab_hack", help="Controlled target repo")
    parser.add_argument(
        "--architecture",
        default="targets/sberlab_architecture.yaml",
        help="Architecture context YAML",
    )
    parser.add_argument(
        "--architecture-overrides",
        help="Optional YAML overrides for automatically derived architecture context",
    )
    parser.add_argument("--sast-config", default="auto", help="Semgrep config/ruleset")
    parser.add_argument(
        "--output-dir",
        default="artifacts/security-pipeline",
        help="Root directory for SAST, reports, and gate artifacts",
    )
    parser.add_argument(
        "--findings",
        help="Use an existing normalized findings JSON instead of running SAST",
    )
    parser.add_argument(
        "--agent-mode",
        choices=("stub", "llm"),
        help="Override CFT_AGENT_MODE for this run",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=1,
        help="Controlled workflow iteration limit per finding",
    )
    parser.add_argument(
        "--full-reports",
        action="store_true",
        help="Print the full human-readable FinalReport for every finding",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations must be at least 1")

    target = Path(args.target).expanduser().resolve()
    architecture = Path(args.architecture).expanduser().resolve()
    architecture_overrides = (
        Path(args.architecture_overrides).expanduser().resolve()
        if args.architecture_overrides
        else None
    )
    output_dir = Path(args.output_dir).expanduser()
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    if args.agent_mode:
        settings.agent_mode = args.agent_mode
    settings.target_repository_path = target

    print("=" * 72)
    print("CFT SECURITY PIPELINE")
    print("=" * 72)
    print(f"Target: {target}")
    print(f"Agent mode: {settings.agent_mode}")

    stage_errors: list[str] = []
    # Без списка финдингов дальше проверять нечего, сразу возвращаем фейл гейт
    try:
        findings_path = _resolve_findings(
            existing_findings=args.findings,
            target=target,
            sast_config=args.sast_config,
            output_dir=output_dir / "sast",
        )
    except (SemgrepError, OSError, ValueError, TypeError) as exc:
        stage_errors.append(f"SAST stage failed: {type(exc).__name__}: {exc}")
        gate = evaluate_gate([], stage_errors=stage_errors)
        _write_gate(output_dir, gate)
        _print_gate(gate)
        return gate.exit_code

    findings = JsonFindingRepository(findings_path).list_findings()
    print(f"Findings to verify: {len(findings)}")

    reports: list[FinalReport] = []
    report_paths: dict[str, str] = {}
    graph = build_graph()

    for index, finding in enumerate(findings):
        print()
        location = f"{finding.file}:{finding.line_start or '?'}"
        print(f"[{index + 1}/{len(findings)}] {finding.rule_id} | {location}")
        try:
            state = build_real_initial_state(
                findings_path=findings_path,
                target_root=target,
                architecture_path=architecture,
                architecture_overrides_path=architecture_overrides,
                finding_id=finding.id,
                max_iterations=args.max_iterations,
            )
            result = graph.invoke(state)
            report = result["final_report"]
            report_path = reports_dir / f"{index:03d}-{_safe_name(finding.id)}.json"
            report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
            reports.append(report)
            report_paths[report.finding_id] = str(report_path)

            print(
                "  "
                f"status={report.status} "
                f"capability={report.verification.capability or 'none'} "
                f"context={report.context_priority.level if report.context_priority else 'N/A'}"
            )
            if args.full_reports:
                print()
                print(render_final_report(report))
        # Падение одного финдинга не прячет остальные, но гейт это обязательно увидит
        except Exception as exc:  # noqa: BLE001 - граница этапа должна стать ошибкой гейта
            message = f"{finding.id}: {type(exc).__name__}: {exc}"
            stage_errors.append(message)
            print(f"  ERROR: {message}")

    gate = evaluate_gate(reports, stage_errors=stage_errors, report_paths=report_paths)
    _write_index(output_dir, reports, report_paths)
    _write_gate(output_dir, gate)
    _print_gate(gate)
    return gate.exit_code


def _resolve_findings(
    *,
    existing_findings: str | None,
    target: Path,
    sast_config: str,
    output_dir: Path,
) -> Path:
    if existing_findings:
        path = Path(existing_findings).expanduser()
        JsonFindingRepository(path).list_findings()
        print(f"SAST: using existing normalized findings: {path}")
        return path

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"SAST: scanning with Semgrep config={sast_config}")
    scan = run_semgrep_scan(target, config=sast_config, timeout_seconds=600)

    raw_path = output_dir / "semgrep.json"
    findings_path = output_dir / "findings.json"
    summary_path = output_dir / "summary.json"
    raw_path.write_text(json.dumps(scan.raw, ensure_ascii=False, indent=2), encoding="utf-8")
    findings_path.write_text(
        json.dumps(
            [finding.model_dump(mode="json") for finding in scan.findings],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = {
        "total": len(scan.findings),
        "by_severity": dict(
            sorted(
                Counter(
                    (finding.severity or "UNKNOWN").upper() for finding in scan.findings
                ).items()
            )
        ),
        "by_service": dict(
            sorted(Counter(finding.service or "unknown" for finding in scan.findings).items())
        ),
        "semgrep_errors": len(scan.raw.get("errors", []))
        if isinstance(scan.raw.get("errors", []), list)
        else 0,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SAST findings: {summary['total']} -> {findings_path}")
    return findings_path


def _write_index(
    output_dir: Path,
    reports: list[FinalReport],
    report_paths: dict[str, str],
) -> None:
    payload = [
        {
            "finding_id": report.finding_id,
            "status": report.status,
            "report_path": report_paths.get(report.finding_id),
        }
        for report in reports
    ]
    (output_dir / "reports-index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_gate(output_dir: Path, gate: GateResult) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "gate.json").write_text(gate.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _print_gate(gate: GateResult) -> None:
    print()
    print("=" * 72)
    print(f"CI/CD GATE: {gate.decision.upper()}")
    print("=" * 72)
    print(
        f"reports={gate.reports_total} confirmed={gate.confirmed} rejected={gate.rejected} "
        f"inconclusive={gate.inconclusive} policy_blocked={gate.policy_blocked}"
    )
    for reason in gate.reasons:
        print(f"- {reason}")
    for error in gate.stage_errors:
        print(f"- stage error: {error}")

    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        annotation = "error" if gate.decision == "fail" else "warning"
        if gate.decision != "pass":
            for reason in gate.reasons:
                print(f"::{annotation}::{reason}")
        for error in gate.stage_errors:
            print(f"::error::{error}")

    print(f"exit_code={gate.exit_code}")


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
    return safe[:140].strip("-.") or "finding"


if __name__ == "__main__":
    raise SystemExit(main())
