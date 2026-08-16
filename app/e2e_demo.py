from __future__ import annotations

import argparse
from pathlib import Path

from agent.graph import build_graph
from app.e2e_inputs import build_real_initial_state


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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    state = build_real_initial_state(
        findings_path=Path(args.findings),
        target_root=Path(args.target),
        architecture_path=Path(args.architecture),
        finding_id=args.finding_id,
        finding_index=args.index,
        max_iterations=args.max_iterations,
    )

    finding = state["finding"]
    print(f"Selected finding: {finding.id}")
    print(f"Location: {finding.file}:{finding.line_start or '?'}")
    print(f"Service: {finding.service or 'unknown'}")
    print("Context source: real target repository + architecture YAML")

    result = build_graph().invoke(state)
    report = result["final_report"]

    print("Workflow status:", report.status)
    validation = result.get("validation")
    if validation is not None:
        decision = "APPROVE" if validation.approved else "DENY"
        print(f"Validator: {decision} - {validation.reason}")
    print("Iterations:", report.iterations)
    print("Evidence count:", len(report.evidence))
    print("CVSS:", report.cvss.severity if report.cvss else "missing")
    print(
        "Context priority:",
        report.context_priority.level if report.context_priority else "missing",
    )
    print("Explanation:", report.explanation)

    if report.status == "confirmed":
        print(
            "WARNING: a real finding must only become confirmed from capability-specific "
            "Evidence semantics."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
