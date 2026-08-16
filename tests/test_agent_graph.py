from agent.graph import build_graph
from executor.executor import SafeExecutor
from schemas.execution import ExecutionResult
from schemas.finding import Finding


def _finding(
    *,
    severity: str,
    service: str = "backend",
) -> Finding:
    return Finding(
        id=f"test-{severity.lower()}",
        source="semgrep",
        rule_id="test.rule",
        title="Synthetic workflow test",
        description="Only for deterministic integration tests.",
        file="backend/example.py",
        line_start=1,
        line_end=2,
        severity=severity,
        service=service,
    )


def _run(
    finding: Finding,
    max_iterations: int = 2,
):
    graph = build_graph()

    return graph.invoke(
        {
            "finding": finding,
            "evidence": [],
            "iteration_count": 0,
            "max_iterations": max_iterations,
        }
    )


def test_graph_confirmed_path() -> None:
    result = _run(
        _finding(severity="TEST_CONFIRMED")
    )
    report = result["final_report"]

    assert report.status == "confirmed"
    assert report.iterations == 1
    assert len(report.evidence) == 1
    assert "read from persistent storage" in report.evidence[0].summary
    assert report.evidence[0].reliability == "high"


def test_graph_rejected_path() -> None:
    result = _run(
        _finding(severity="TEST_REJECTED")
    )
    report = result["final_report"]

    assert report.status == "rejected"
    assert report.iterations == 1
    assert len(report.evidence) == 1


def test_graph_policy_blocked_path() -> None:
    result = _run(
        _finding(
            severity="TEST_CONFIRMED",
            service="force-deny",
        )
    )
    report = result["final_report"]

    assert report.status == "policy_blocked"
    assert report.iterations == 1
    assert len(report.evidence) == 0


def test_graph_inconclusive_retries_until_limit() -> None:
    result = _run(
        _finding(severity="TEST_INCONCLUSIVE"),
        max_iterations=3,
    )
    report = result["final_report"]

    assert report.status == "inconclusive"
    assert report.iterations == 3
    assert len(report.evidence) == 3


class _SequencedExecutor:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = results
        self.calls = 0

    def execute(self, action) -> ExecutionResult:
        result = self.results[min(self.calls, len(self.results) - 1)].model_copy(
            update={"action_id": action.id}
        )
        self.calls += 1
        return result


def _execution_result(
    *,
    run_id: str,
    status: str,
    exit_code: int,
    timed_out: bool,
) -> ExecutionResult:
    return ExecutionResult(
        run_id=run_id,
        action_id="replaced-by-test-executor",
        status=status,
        exit_code=exit_code,
        stdout="",
        stderr="controlled failure" if exit_code else "",
        timed_out=timed_out,
        evidence_ref=f"execution-unavailable-{run_id}",
        audit_ref=f"audit:{run_id}",
        duration_ms=100,
    )


def test_one_timed_out_run_does_not_break_pipeline(monkeypatch) -> None:
    executor = _SequencedExecutor(
        [
            _execution_result(
                run_id="timeout",
                status="failed",
                exit_code=124,
                timed_out=True,
            ),
            _execution_result(
                run_id="recovery",
                status="completed",
                exit_code=0,
                timed_out=False,
            ),
        ]
    )
    monkeypatch.setattr(
        SafeExecutor,
        "from_config",
        classmethod(lambda cls, **kwargs: executor),
    )

    result = _run(_finding(severity="TEST_CONFIRMED"), max_iterations=2)

    assert result["final_report"].status == "confirmed"
    assert result["final_report"].iterations == 2
    assert len(result["final_report"].evidence) == 2


def test_repeated_failed_runs_end_cleanly_at_iteration_limit(monkeypatch) -> None:
    executor = _SequencedExecutor(
        [
            _execution_result(
                run_id="error",
                status="failed",
                exit_code=7,
                timed_out=False,
            )
        ]
    )
    monkeypatch.setattr(
        SafeExecutor,
        "from_config",
        classmethod(lambda cls, **kwargs: executor),
    )

    result = _run(_finding(severity="TEST_CONFIRMED"), max_iterations=2)

    assert result["final_report"].status == "inconclusive"
    assert result["final_report"].iterations == 2
    assert len(result["final_report"].evidence) == 2
