from agent.graph import build_graph
from schemas.finding import Finding


def main() -> None:
    graph = build_graph()

    initial_state = {
        "finding": Finding(
            id="demo-001",
            source="semgrep",
            rule_id="demo.rule",
            title="Demo controlled finding",
            description="Synthetic finding used only to validate the workflow.",
            file="backend/example.py",
            line_start=10,
            line_end=12,
            severity="TEST_CONFIRMED",
            service="backend",
        ),
        "evidence": [],
        "iteration_count": 0,
        "max_iterations": 2,
    }

    result = graph.invoke(initial_state)
    report = result["final_report"]

    print("Workflow status:", report.status)
    print("Finding:", report.finding_id)
    print("Iterations:", report.iterations)
    print("Evidence count:", len(report.evidence))
    print("Explanation:", report.explanation)


if __name__ == "__main__":
    main()
