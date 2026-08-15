from agent.graph import build_graph
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
