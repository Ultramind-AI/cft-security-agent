import sys

import pytest

from fix_verification.proposal import PatchProposalService
from fix_verification.service import FixVerificationService
from schemas.fix import FixCheck, ProposedPatch
from schemas.report import FinalReport, ReportFinding, VerificationSummary


def _report(*, status: str = "confirmed") -> FinalReport:
    return FinalReport(
        finding_id="finding-1",
        finding=ReportFinding(
            id="finding-1",
            source="semgrep",
            rule_id="demo.rule",
            title="Unsafe flag",
            description="The flag is unsafe.",
            file="app.py",
            line_start=1,
        ),
        status=status,
        code_context="safe = False",
        verification=VerificationSummary(),
        explanation="test",
        next_step="test",
    )


def _proposal() -> ProposedPatch:
    return ProposedPatch(
        finding_id="finding-1",
        rationale="Enable the safe mode.",
        unified_diff="""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-safe = False
+safe = True
""",
    )


def _check(check_id: str, kind: str) -> FixCheck:
    return FixCheck(
        id=check_id,
        kind=kind,
        argv=[
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('app.py').read_text() == 'safe = True\\n'",
        ],
    )


def test_verified_fix_runs_in_temporary_copy_and_preserves_source(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    source = target / "app.py"
    source.write_text("safe = False\n", encoding="utf-8")

    artifact = FixVerificationService().verify(
        report=_report(),
        proposal=_proposal(),
        target=target,
        checks=[_check("static", "static"), _check("runtime", "runtime")],
    )

    assert artifact.patch_application.status == "applied"
    assert artifact.verdict == "verified"
    assert [item.status for item in artifact.re_test_results] == ["passed", "passed"]
    assert len(artifact.new_evidence) == 2
    assert source.read_text(encoding="utf-8") == "safe = False\n"


def test_fix_without_runtime_evidence_is_inconclusive(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = False\n", encoding="utf-8")

    artifact = FixVerificationService().verify(
        report=_report(),
        proposal=_proposal(),
        target=target,
        checks=[_check("static", "static")],
    )

    assert artifact.verdict == "inconclusive"


def test_fix_is_refused_for_non_confirmed_finding(tmp_path) -> None:
    with pytest.raises(ValueError, match="CONFIRMED"):
        FixVerificationService().verify(
            report=_report(status="inconclusive"),
            proposal=_proposal(),
            target=tmp_path,
            checks=[],
        )


def test_patch_outside_finding_file_is_rejected_without_source_changes(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    source = target / "app.py"
    source.write_text("safe = False\n", encoding="utf-8")
    proposal = _proposal().model_copy(
        update={
            "unified_diff": _proposal().unified_diff.replace("app.py", "other.py")
        }
    )

    artifact = FixVerificationService().verify(
        report=_report(),
        proposal=proposal,
        target=target,
        checks=[],
    )

    assert artifact.patch_application.status == "rejected"
    assert artifact.verdict == "not_verified"
    assert source.read_text(encoding="utf-8") == "safe = False\n"


def test_llm_patch_proposer_returns_artifact_and_has_no_execution_access() -> None:
    class FakeClient:
        def complete_model(self, **kwargs):
            assert kwargs["operation"] == "propose_patch"
            assert "commands" not in kwargs["user_payload"]
            return _proposal()

    proposal = PatchProposalService(FakeClient()).propose(_report())

    assert proposal.finding_id == "finding-1"
    assert proposal.unified_diff.startswith("diff --git")
