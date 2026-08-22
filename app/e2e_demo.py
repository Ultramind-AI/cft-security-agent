from __future__ import annotations

import argparse
from pathlib import Path

from agent.graph import build_graph
from app.config import settings
from app.e2e_inputs import build_real_initial_state
from reporting.presentation import render_final_report
from schemas.target import TargetProfile


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the bounded agent workflow with a normalized SAST finding, "
            "real source context and target architecture context."
        )
    )
    parser.add_argument(
        "--findings",
        default="reports/sast/findings.json",
        help="Normalized findings.json produced by app.sast_scan",
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
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--finding-id", help="Exact normalized finding id")
    selection.add_argument(
        "--index",
        type=int,
        default=0,
        help="Zero-based finding index when --finding-id is not supplied",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=1,
        help="Controlled workflow iteration limit for this demo run",
    )
    parser.add_argument(
        "--report-json",
        help="Optional path for the machine-readable FinalReport JSON artifact",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    profile_path = Path(args.profile).expanduser()
    profile = TargetProfile.from_yaml(
        profile_path,
        repository_path_override=args.target,
        base_url_override=settings.target_base_url,
    )
    if profile.repository_path is None:
        raise SystemExit("TargetProfile.repository_path or --target is required")
    architecture_value = args.architecture or profile.architecture.file
    if architecture_value is None:
        raise SystemExit("TargetProfile.architecture.file or --architecture is required")

    settings.target_file = profile_path
    settings.target_repository_path = profile.repository_path
    state = build_real_initial_state(
        findings_path=Path(args.findings),
        target_root=profile.repository_path,
        architecture_path=Path(architecture_value),
        target_profile=profile,
        finding_id=args.finding_id,
        finding_index=args.index,
        max_iterations=args.max_iterations,
    )

    finding = state["finding"]
    print(f"Target profile: {profile.id}")
    print(f"Selected finding: {finding.id}")
    print(f"Location: {finding.file}:{finding.line_start or '?'}")
    print(f"Service: {finding.service or 'unknown'}")
    print("Context source: target repository + TargetProfile + architecture YAML")
    print(f"Agent mode: {settings.agent_mode}")

    result = build_graph().invoke(state)
    report = result["final_report"]
    print()
    print(render_final_report(report))

    if args.report_json:
        report_path = Path(args.report_json).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report.model_dump_json(indent=2) + "\n")
        print(f"Report JSON: {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
