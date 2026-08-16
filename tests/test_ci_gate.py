from pipeline.gate import evaluate_gate
from schemas.pipeline import GateResult
from schemas.report import FinalReport, ReportFinding, VerificationSummary
from schemas.scoring import ContextPriority, CVSSResult


def _report(
    *,
    status: str,
    context: str | None = "MEDIUM",
    cvss: str | None = "N/A",
    finding_id: str = "finding-1",
) -> FinalReport:
    return FinalReport(
        finding_id=finding_id,
        finding=ReportFinding(
            id=finding_id,
            source="semgrep",
            rule_id="demo.rule",
            title="Demo finding",
            severity="WARNING",
            service="backend",
            file="demo.py",
            line_start=1,
            line_end=1,
        ),
        status=status,
        verification=VerificationSummary(),
        cvss=(
            CVSSResult(vector="N/A", score=None, severity=cvss, reasoning="test")
            if cvss is not None
            else None
        ),
        context_priority=(
            ContextPriority(level=context, score=5.0, reasons=[])
            if context is not None
            else None
        ),
        explanation="test",
        next_step="test",
    )


def test_no_findings_passes() -> None:
    gate = evaluate_gate([])

    assert isinstance(gate, GateResult)
    assert gate.decision == "pass"
    assert gate.exit_code == 0
    assert gate.reports_total == 0


def test_rejected_finding_passes() -> None:
    gate = evaluate_gate([_report(status="rejected", context="HIGH")])

    assert gate.decision == "pass"
    assert gate.exit_code == 0
    assert gate.rejected == 1


def test_confirmed_high_context_fails() -> None:
    gate = evaluate_gate([_report(status="confirmed", context="HIGH")])

    assert gate.decision == "fail"
    assert gate.exit_code == 1
    assert gate.findings[0].gate_effect == "fail"
    assert "context priority HIGH" in gate.findings[0].reason


def test_confirmed_high_cvss_fails_even_with_medium_context() -> None:
    gate = evaluate_gate([_report(status="confirmed", context="MEDIUM", cvss="HIGH")])

    assert gate.decision == "fail"
    assert gate.exit_code == 1
    assert "CVSS severity HIGH" in gate.findings[0].reason


def test_confirmed_medium_is_non_blocking_warning() -> None:
    gate = evaluate_gate([_report(status="confirmed", context="MEDIUM")])

    assert gate.decision == "warn"
    assert gate.exit_code == 0
    assert gate.findings[0].gate_effect == "warn"


def test_inconclusive_and_policy_blocked_warn() -> None:
    gate = evaluate_gate(
        [
            _report(status="inconclusive", finding_id="a"),
            _report(status="policy_blocked", finding_id="b"),
        ]
    )

    assert gate.decision == "warn"
    assert gate.exit_code == 0
    assert gate.inconclusive == 1
    assert gate.policy_blocked == 1


def test_stage_error_is_mandatory_failure() -> None:
    gate = evaluate_gate(
        [_report(status="rejected")],
        stage_errors=["SAST stage failed: timeout"],
    )

    assert gate.decision == "fail"
    assert gate.exit_code == 2
    assert gate.stage_errors == ["SAST stage failed: timeout"]
