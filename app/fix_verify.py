from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from fix_verification.service import FixVerificationService
from schemas.fix import FixVerificationPlan, ProposedPatch
from schemas.report import FinalReport


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply a proposed fix only in a temporary copy and re-test it."
    )
    parser.add_argument("--report", required=True, help="Confirmed FinalReport JSON")
    parser.add_argument("--patch", required=True, help="Proposed unified diff artifact")
    parser.add_argument("--target", required=True, help="Source target copied read-only")
    parser.add_argument("--plan", required=True, help="Operator-owned YAML re-test plan")
    parser.add_argument("--output", required=True, help="Fix verification JSON artifact")
    parser.add_argument(
        "--rationale",
        default="Externally proposed minimal patch",
        help="Patch rationale stored in the artifact",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = FinalReport.model_validate_json(
        Path(args.report).read_text(encoding="utf-8")
    )
    proposal = ProposedPatch(
        finding_id=report.finding_id,
        rationale=args.rationale,
        unified_diff=Path(args.patch).read_text(encoding="utf-8"),
    )
    plan_data = yaml.safe_load(Path(args.plan).read_text(encoding="utf-8")) or {}
    plan = FixVerificationPlan.model_validate(plan_data)

    artifact = FixVerificationService().verify(
        report=report,
        proposal=proposal,
        target=args.target,
        checks=plan.checks,
    )
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": artifact.verdict, "artifact": str(output)}))
    return 0 if artifact.verdict == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
