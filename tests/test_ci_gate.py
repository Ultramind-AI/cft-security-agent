import json
import subprocess

from pipeline.errors import error_from_exception
from pipeline.gate import evaluate_gate
from schemas.errors import ErrorDetail
from schemas.pipeline import GateResult
from schemas.pr import PRFindingContext
from schemas.report import FinalReport, ReportFinding, VerificationSummary
from schemas.scoring import ContextPriority, CVSSResult


def _report(
    *,
    status: str,
    context: str | None = "MEDIUM",
    cvss: str | None = "N/A",
    finding_id: str = "finding-1",
    pr_classification: str | None = None,
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
            pr_context=(
                PRFindingContext(
                    fingerprint="a" * 64,
                    classification=pr_classification,
                    base_ref="main",
                    head_ref="feature",
                )
                if pr_classification is not None
                else None
            ),
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
    assert gate.decision_basis == "no_blocking_condition"
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
    assert gate.findings[0].category == "confirmed_risk"
    assert gate.decision_basis == "confirmed_risk"
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


def test_new_high_pr_finding_blocks_more_strongly_than_existing_high() -> None:
    new_gate = evaluate_gate(
        [_report(status="confirmed", context="HIGH", pr_classification="new")]
    )
    existing_gate = evaluate_gate(
        [_report(status="confirmed", context="HIGH", pr_classification="existing")]
    )

    assert new_gate.decision == "fail"
    assert existing_gate.decision == "warn"
    assert "pre-existing" in existing_gate.findings[0].reason


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
    assert gate.findings[1].category == "policy_block"
    assert gate.decision_basis == "policy_or_uncertainty"


def test_stage_error_is_mandatory_failure() -> None:
    gate = evaluate_gate(
        [_report(status="rejected")],
        stage_errors=["SAST stage failed: timeout"],
    )

    assert gate.decision == "fail"
    assert gate.exit_code == 2
    assert gate.decision_basis == "technical_pipeline_error"
    assert gate.technical_errors == 1
    assert gate.stage_errors == ["SAST stage failed: timeout"]
    assert gate.errors == [
        ErrorDetail(
            code="INTERNAL_ERROR",
            layer="pipeline",
            message="SAST stage failed: timeout",
        )
    ]


def test_structured_stage_error_is_machine_readable_mandatory_failure() -> None:
    error = ErrorDetail(
        code="TIMEOUT",
        layer="sast",
        message="SAST scan timed out",
        retryable=True,
    )

    gate = evaluate_gate([_report(status="rejected")], errors=[error])
    payload = json.loads(gate.model_dump_json())

    assert gate.decision == "fail"
    assert gate.exit_code == 2
    assert gate.errors == [error]
    assert gate.stage_errors == ["SAST scan timed out"]
    assert payload["errors"] == [
        {
            "code": "TIMEOUT",
            "layer": "sast",
            "message": "SAST scan timed out",
            "retryable": True,
        }
    ]


def test_policy_blocked_is_not_a_system_error() -> None:
    gate = evaluate_gate([_report(status="policy_blocked")])

    assert gate.decision == "warn"
    assert gate.errors == []
    assert gate.stage_errors == []


def test_pipeline_exception_normalizer_classifies_timeout_without_raw_text() -> None:
    raw_secret = "token=must-not-appear"
    error = error_from_exception(
        subprocess.TimeoutExpired(["semgrep"], 5, stderr=raw_secret),
        layer="sast",
        public_message="SAST scan timed out",
    )

    assert error.code == "TIMEOUT"
    assert error.retryable is True
    assert raw_secret not in error.message
