from agent.graph import build_graph
from reporting.builder import _sandbox_actions
from reporting.presentation import render_final_report
from schemas.action import ActionProposal
from schemas.execution import ExecutionResult
from schemas.finding import Finding


def _finding(*, severity: str = "TEST_CONFIRMED", service: str = "backend") -> Finding:
    return Finding(
        id="report-test",
        source="semgrep",
        rule_id="test.rule",
        title="Synthetic report finding",
        description="Only for report tests.",
        file="backend/example.py",
        line_start=7,
        line_end=7,
        severity=severity,
        service=service,
    )


def _run(finding: Finding, max_iterations: int = 1):
    return build_graph().invoke(
        {
            "finding": finding,
            "evidence": [],
            "iteration_count": 0,
            "max_iterations": max_iterations,
        }
    )["final_report"]


def test_final_report_contains_stable_ui_ready_context() -> None:
    report = _run(_finding())

    assert report.schema_version == "1.0"
    assert report.finding.id == "report-test"
    assert report.finding.file == "backend/example.py"
    assert report.finding.description == "Only for report tests."
    assert report.code_context
    assert report.architecture_context is not None
    assert report.analysis_summary
    assert report.hypothesis
    assert report.verification.capability == "safe_noop"
    assert report.verification.validator_decision == "approved"
    assert report.verification.evidence_count == 1
    assert report.sandbox_actions[0].execution_status == "completed"
    assert report.policy_decisions[0].decision == "approved"
    assert report.ci_gate_impact is not None
    assert report.ci_gate_impact.category == "confirmed_risk"
    assert report.next_step


def test_policy_blocked_report_explains_basis_and_limitation() -> None:
    report = _run(_finding(service="force-deny"))

    assert report.status == "policy_blocked"
    assert report.verification.validator_decision == "denied"
    assert report.verification.decision_basis == "validator_policy"
    assert report.sandbox_actions[0].execution_status is None
    assert report.policy_decisions[0].decision == "denied"
    assert report.ci_gate_impact is not None
    assert report.ci_gate_impact.category == "policy_block"
    assert any("No verification action was executed" in item for item in report.limitations)


def test_report_renderer_is_demo_friendly() -> None:
    report = _run(_finding())
    rendered = render_final_report(report)

    assert "FINAL SECURITY REPORT" in rendered
    assert "Status: CONFIRMED" in rendered
    assert "Finding" in rendered
    assert "Verification" in rendered
    assert "Context" in rendered
    assert "Sandbox actions (1)" in rendered
    assert "Policy decisions (1)" in rendered
    assert "Evidence (1)" in rendered
    assert "Risk" in rendered
    assert "Conclusion" in rendered
    assert "CI Gate impact" in rendered
    assert "Next step" in rendered


def test_terminal_report_does_not_overclaim_agent_hypothesis() -> None:
    report = _run(_finding())

    assert report.status == "confirmed"
    assert "reported security condition" in report.explanation
    assert "verification hypothesis" not in report.explanation


def test_sandbox_command_summary_is_bounded_redacted_and_ui_ready() -> None:
    action = ActionProposal(
        id="action-1",
        tool="sandbox_command",
        target="demo",
        environment="local",
        parameters={
            "argv": ["python", "/Users/alice/project/check.py", "token=secret-value"],
            "cwd": "/Users/alice/project",
        },
        purpose="Inspect source",
        expected_evidence="Command output",
    )
    execution = ExecutionResult(
        run_id="run-1",
        action_id="action-1",
        status="completed",
        exit_code=0,
        stdout="line one\npassword=hunter2\nline three",
        evidence_ref="evidence.json",
        audit_ref="audit.jsonl",
        duration_ms=17,
    )

    summary = _sandbox_actions(
        {
            "proposed_action": action,
            "execution": execution,
            "sandbox_session_id": "sandbox-1",
        }
    )[0]

    assert summary.command == ["python", "<home>/project/check.py", "token=<redacted>"]
    assert summary.cwd == "<home>/project"
    assert summary.stdout == "line one\npassword=<redacted>\nline three"
    assert summary.duration_ms == 17
    assert summary.sandbox_session_id == "sandbox-1"
