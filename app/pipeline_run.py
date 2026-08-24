from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from agent.graph import build_graph
from app.config import settings
from app.e2e_inputs import build_real_initial_state
from architecture.service import ArchitectureService
from pipeline.cancellation import RunCancelled, check_cancelled
from pipeline.errors import error_from_exception
from pipeline.gate import evaluate_gate
from pipeline.policy import classify_finding_gate
from pipeline.progress import PipelineProgressRecorder
from pr_analysis.git_diff import read_git_diff
from pr_analysis.service import PRAnalysisService
from reporting.presentation import render_final_report
from sast.repository import JsonFindingRepository
from sast.semgrep_runner import SemgrepError, run_semgrep_scan
from schemas.errors import ErrorDetail
from schemas.pipeline import GateResult
from schemas.report import CIGateImpact, FinalReport, ReportFinding, VerificationSummary
from schemas.runtime import RuntimeServiceMap
from schemas.target import TargetProfile
from tools.runtime import LocalCodeReader


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run SAST, verify every normalized finding with the bounded agent workflow, "
            "write FinalReport JSON artifacts, and return a deterministic CI/CD gate."
        )
    )
    parser.add_argument(
        "--profile",
        default=str(settings.target_file),
        help="TargetProfile YAML",
    )
    parser.add_argument(
        "--target",
        help="Optional repository root override from TargetProfile",
    )
    parser.add_argument(
        "--architecture",
        help="Optional architecture YAML override from TargetProfile",
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
    parser.add_argument(
        "--base-ref",
        help="Base Git ref for optional PR-aware diff analysis",
    )
    parser.add_argument(
        "--head-ref",
        default="HEAD",
        help="Head Git ref for PR-aware diff analysis (default: HEAD)",
    )
    parser.add_argument(
        "--base-findings",
        help="Normalized findings JSON produced for --base-ref",
    )
    parser.add_argument(
        "--base-architecture",
        help="Optional base-revision architecture YAML for context comparison",
    )
    return parser.parse_args()


def run_pipeline(
    args: argparse.Namespace,
    *,
    profile_override: TargetProfile | None = None,
    runtime_services: RuntimeServiceMap | None = None,
) -> int:
    """Run the SAST -> agent -> FinalReport -> Gate part of the pipeline.

    The CI orchestrator passes a discovered profile and the service map of an
    already running sandbox.  The ordinary CLI can still call this function
    without either value and keeps its previous behaviour.
    """
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations must be at least 1")

    check_cancelled(getattr(args, "cancellation_token", None))
    profile_path = Path(args.profile).expanduser()
    profile = profile_override or TargetProfile.from_yaml(
        profile_path,
        repository_path_override=args.target,
        base_url_override=settings.target_base_url,
    )
    if profile.repository_path is None:
        raise SystemExit("TargetProfile.repository_path or --target is required")
    architecture_value = args.architecture or profile.architecture.file
    if architecture_value is None:
        raise SystemExit("TargetProfile.architecture.file or --architecture is required")

    target = profile.repository_path
    architecture = Path(architecture_value).expanduser().resolve()
    architecture_overrides = (
        Path(args.architecture_overrides).expanduser().resolve()
        if args.architecture_overrides
        else None
    )
    output_dir = Path(args.output_dir).expanduser()
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    progress = PipelineProgressRecorder(output_dir)

    if args.agent_mode:
        settings.agent_mode = args.agent_mode
    settings.target_file = profile_path
    settings.target_repository_path = target

    print("=" * 72)
    print("CFT SECURITY PIPELINE")
    print("=" * 72)
    print(f"Target profile: {profile.id}")
    print(f"Target: {target}")
    print(f"Agent mode: {settings.agent_mode}")

    pipeline_errors: list[ErrorDetail] = []
    # Без списка финдингов дальше проверять нечего, сразу возвращаем фейл гейт
    try:
        check_cancelled()
        findings_path = _resolve_findings(
            existing_findings=args.findings,
            target=target,
            sast_config=args.sast_config,
            output_dir=output_dir / "sast",
            service_resolver=profile.resolve_service,
        )
        check_cancelled()
    except RunCancelled:
        raise
    except (SemgrepError, OSError, ValueError, TypeError) as exc:
        pipeline_errors.append(
            error_from_exception(
                exc,
                layer="sast",
                public_message="SAST stage failed",
            )
        )
        gate = evaluate_gate([], errors=pipeline_errors)
        _write_gate(output_dir, gate)
        _print_gate(gate)
        return gate.exit_code

    findings = JsonFindingRepository(findings_path).list_findings()
    progress.sast_done(len(findings))
    if args.base_ref:
        if not args.base_findings:
            pipeline_errors.append(
                ErrorDetail(
                    code="VALIDATION_ERROR",
                    layer="pipeline",
                    message=(
                        "PR analysis requires --base-findings to distinguish new and "
                        "existing findings."
                    ),
                )
            )
        else:
            try:
                base_findings = JsonFindingRepository(args.base_findings).list_findings()
                diff = read_git_diff(target, base_ref=args.base_ref, head_ref=args.head_ref)
                base_architecture = (
                    ArchitectureService(args.base_architecture)
                    if args.base_architecture
                    else None
                )
                analyser = PRAnalysisService(
                    base_ref=args.base_ref,
                    head_ref=args.head_ref,
                    diff=diff,
                    base_architecture=base_architecture,
                    head_architecture=ArchitectureService(
                        architecture,
                        overrides_path=architecture_overrides,
                    ),
                )
                findings, pr_summary = analyser.analyse(
                    base_findings=base_findings,
                    head_findings=findings,
                )
                (output_dir / "pr-analysis.json").write_text(
                    pr_summary.model_dump_json(indent=2) + "\n",
                    encoding="utf-8",
                )
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                pipeline_errors.append(
                    error_from_exception(
                        exc,
                        layer="pipeline",
                        public_message="PR analysis failed",
                    )
                )
    print(f"Findings to verify: {len(findings)}")

    reports: list[FinalReport] = []
    report_paths: dict[str, str] = {}
    graph = build_graph()
    if findings:
        progress.verification_started()

    for index, finding in enumerate(findings):
        check_cancelled()
        print()
        location = f"{finding.file}:{finding.line_start or '?'}"
        print(f"[{index + 1}/{len(findings)}] {finding.rule_id} | {location}")
        progress.finding_started(
            index=index + 1,
            total=len(findings),
            finding_id=finding.id,
            title=finding.title,
            severity=finding.severity,
            rule_id=finding.rule_id,
            file=finding.file,
        )
        try:
            service = profile.resolve_service(finding.file) or finding.service
            if not service:
                # Репозиторные файлы (.github, корневые конфиги) не принадлежат
                # runtime-сервису, поэтому для них нельзя выдумывать dynamic-проверку.
                report = _build_static_finding_report(
                    finding,
                    target,
                    reason=(
                        "The finding belongs to the repository rather than a discovered "
                        "runtime service, so no sandbox action was selected."
                    ),
                )
                report_path = reports_dir / f"{index:03d}-{_safe_name(finding.id)}.json"
                report_path.write_text(
                    report.model_dump_json(indent=2) + "\n",
                    encoding="utf-8",
                )
                reports.append(report)
                report_paths[report.finding_id] = str(report_path)
                progress.finding_finished(
                    finding_id=finding.id,
                    status=report.status,
                )
                print("  status=inconclusive capability=none context=repository")
                continue

            unsupported_reason = _unsupported_verification_reason(profile, finding)
            if unsupported_reason is not None:
                report = _build_static_finding_report(
                    finding,
                    target,
                    reason=unsupported_reason,
                )
                report_path = reports_dir / f"{index:03d}-{_safe_name(finding.id)}.json"
                report_path.write_text(
                    report.model_dump_json(indent=2) + "\n",
                    encoding="utf-8",
                )
                reports.append(report)
                report_paths[report.finding_id] = str(report_path)
                progress.finding_finished(
                    finding_id=finding.id,
                    status=report.status,
                )
                print("  status=inconclusive capability=none context=unmapped")
                continue

            state = build_real_initial_state(
                findings_path=findings_path,
                target_root=target,
                architecture_path=architecture,
                target_profile=profile,
                architecture_overrides_path=architecture_overrides,
                finding_id=finding.id,
                max_iterations=args.max_iterations,
            )
            if runtime_services is not None:
                # Все действия одного CI-запуска используют уже поднятую sandbox-сессию.
                state["runtime_services"] = runtime_services
            # build_real_initial_state loads the source artifact; PR metadata is an
            # additive pipeline concern and is attached without rewriting that artifact.
            state["finding"] = finding
            result = graph.invoke(state)
            check_cancelled()
            report = result["final_report"]
            report_path = reports_dir / f"{index:03d}-{_safe_name(finding.id)}.json"
            report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
            reports.append(report)
            report_paths[report.finding_id] = str(report_path)
            progress.finding_finished(finding_id=finding.id, status=report.status)

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
        except RunCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - граница этапа должна стать ошибкой гейта
            error = error_from_exception(
                exc,
                layer="pipeline",
                public_message=f"Finding workflow failed: {finding.id}",
            )
            pipeline_errors.append(error)
            progress.finding_finished(finding_id=finding.id, status="error")
            print(f"  ERROR [{error.code}/{error.layer}]: {error.message}")

    check_cancelled()
    gate = evaluate_gate(reports, errors=pipeline_errors, report_paths=report_paths)
    _write_index(output_dir, reports, report_paths)
    _write_gate(output_dir, gate)
    _print_gate(gate)
    return gate.exit_code


def _build_static_finding_report(
    finding,
    target: Path,
    *,
    reason: str,
) -> FinalReport:
    """Create an auditable report when no trusted active verification is available."""

    code = LocalCodeReader(target).read_code(
        finding.file,
        finding.line_start,
        finding.line_end,
    )
    gate = classify_finding_gate(
        finding_id=finding.id,
        status="inconclusive",
        context_level=None,
        cvss_severity=None,
        pr_classification=(
            finding.pr_context.classification
            if finding.pr_context is not None
            else None
        ),
    )
    return FinalReport(
        finding_id=finding.id,
        finding=ReportFinding(
            id=finding.id,
            source=finding.source,
            rule_id=finding.rule_id,
            title=finding.title,
            description=finding.description,
            severity=finding.severity,
            service=finding.service,
            file=finding.file,
            line_start=finding.line_start,
            line_end=finding.line_end,
            pr_context=finding.pr_context,
        ),
        status="inconclusive",
        analysis_summary=(
            "SAST recorded a source finding. It is preserved as a static observation "
            "and was not converted into an unsupported runtime claim."
        ),
        code_context=code.content,
        verification=VerificationSummary(
            validator_decision="not_run",
            evidence_count=0,
            decision_basis="workflow_state",
        ),
        ci_gate_impact=CIGateImpact(
            effect=gate.effect,
            category=gate.category,
            reason=gate.reason,
        ),
        explanation=reason,
        limitations=[
            "The static SAST observation was not confirmed by a runtime capability."
        ],
        next_step=(
            "Add the required trusted artifact mapping or review the source finding, "
            "then rerun the analysis."
        ),
        iterations=0,
    )


def _unsupported_verification_reason(profile: TargetProfile, finding) -> str | None:
    """Return why a specialized capability cannot be bound to trusted profile data."""

    rule_id = finding.rule_id.lower()
    required_paths: list[tuple[str, str | None]] = []
    required_roles: list[str] = []
    required_metadata: list[str] = []

    if rule_id.startswith("dockerfile.security.missing-user"):
        required_paths.append((finding.file, "dockerfile"))
    elif "unvalidated-password" in rule_id:
        required_paths.append((finding.file, "python"))
    elif "react-dangerouslysetinnerhtml" in rule_id:
        required_paths.append((finding.file, None))
        required_roles.extend(
            [
                "react_html_flow.model",
                "react_html_flow.serializer",
                "react_html_flow.view",
            ]
        )
        required_metadata.append("react_html_flow.field")
    else:
        return None

    missing: list[str] = []
    for path, kind in required_paths:
        try:
            profile.artifact_id_for_path(path, kind=kind)
        except ValueError:
            missing.append(f"artifact:{path}")
    for role in required_roles:
        try:
            profile.artifact_id_for_role(role)
        except ValueError:
            missing.append(f"role:{role}")
    for key in required_metadata:
        if not profile.metadata.get(key):
            missing.append(f"metadata:{key}")

    if not missing:
        return None
    return (
        "The target profile does not map the trusted inputs required by this "
        f"specialized verification capability ({', '.join(missing)}). The SAST "
        "finding remains inconclusive; Validator and Executor were not bypassed."
    )


def main() -> int:
    return run_pipeline(_parse_args())


def _resolve_findings(
    *,
    existing_findings: str | None,
    target: Path,
    sast_config: str,
    output_dir: Path,
    service_resolver: Callable[[str], str | None],
) -> Path:
    if existing_findings:
        path = Path(existing_findings).expanduser()
        JsonFindingRepository(path).list_findings()
        print(f"SAST: using existing normalized findings: {path}")
        return path

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"SAST: scanning with Semgrep config={sast_config}")
    scan = run_semgrep_scan(
        target,
        config=sast_config,
        timeout_seconds=600,
        service_resolver=service_resolver,
    )

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
    for error in gate.errors:
        print(f"- stage error [{error.code}/{error.layer}]: {error.message}")

    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        annotation = "error" if gate.decision == "fail" else "warning"
        if gate.decision != "pass":
            for reason in gate.reasons:
                print(f"::{annotation}::{reason}")
        for error in gate.errors:
            print(f"::error::[{error.code}/{error.layer}] {error.message}")

    print(f"exit_code={gate.exit_code}")


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)
    return safe[:140].strip("-.") or "finding"


if __name__ == "__main__":
    raise SystemExit(main())
