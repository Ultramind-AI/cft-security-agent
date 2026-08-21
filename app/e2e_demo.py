from __future__ import annotations

import argparse
from pathlib import Path

from agent.graph import build_graph
from app.config import settings
from app.e2e_inputs import build_real_initial_state
from reporting.presentation import render_final_report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the current bounded agent workflow with a real normalized SAST finding, "
            "real source context and real architecture context."
        )
    )
    parser.add_argument(
        "--findings",
        default="reports/sast/findings.json",
        help="Normalized findings.json produced by app.sast_scan",
    )
    parser.add_argument(
        "--target",
        default="../sberlab_hack",
        help="Local path to the controlled SberLab source repository",
    )
    parser.add_argument(
        "--architecture",
        default="targets/sberlab_architecture.yaml",
        help="Architecture context YAML",
    )
    parser.add_argument(
        "--architecture-overrides",
        help="Optional YAML overrides for automatically derived architecture context",
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
        help=(
            "Optional path for the machine-readable FinalReport JSON artifact. "
            "Parent directories are created automatically."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    settings.target_repository_path = Path(args.target).expanduser().resolve()
    state = build_real_initial_state(
        findings_path=Path(args.findings),
        target_root=Path(args.target),
        architecture_path=Path(args.architecture),
        architecture_overrides_path=(
            Path(args.architecture_overrides)
            if args.architecture_overrides
            else None
        ),
        finding_id=args.finding_id,
        finding_index=args.index,
        max_iterations=args.max_iterations,
    )

    finding = state["finding"]
    print(f"Selected finding: {finding.id}")
    print(f"Location: {finding.file}:{finding.line_start or '?'}")
    print(f"Service: {finding.service or 'unknown'}")
    print("Context source: project description + optional architecture overrides")
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
