from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    analyse,
    build_report,
    collect_evidence,
    execute_action,
    form_hypothesis,
    load_context,
    propose_action,
    reevaluate,
    score_finding,
    validate_action,
)
from schemas.state import AgentState


def _after_validation(state: AgentState) -> str:
    if state["validation"].approved:
        return "execute"
    # Отказ валидатора завершает ветку: обход политики не предусмотрен
    return "report"


def _after_reevaluation(state: AgentState) -> str:
    if state.get("status") == "continue":
        # Повторный круг всегда остается под общим лимитом итераций
        return "analyse"
    return "report"


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("load_context", load_context)
    graph.add_node("score_finding", score_finding)
    graph.add_node("analyse", analyse)
    graph.add_node("form_hypothesis", form_hypothesis)
    graph.add_node("propose_action", propose_action)
    graph.add_node("validate_action", validate_action)
    graph.add_node("execute", execute_action)
    graph.add_node("collect_evidence", collect_evidence)
    graph.add_node("reevaluate", reevaluate)
    graph.add_node("report", build_report)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "score_finding")
    graph.add_edge("score_finding", "analyse")
    graph.add_edge("analyse", "form_hypothesis")
    graph.add_edge("form_hypothesis", "propose_action")
    graph.add_edge("propose_action", "validate_action")

    graph.add_conditional_edges(
        "validate_action",
        _after_validation,
        {
            "execute": "execute",
            "report": "report",
        },
    )

    graph.add_edge("execute", "collect_evidence")
    graph.add_edge("collect_evidence", "reevaluate")

    graph.add_conditional_edges(
        "reevaluate",
        _after_reevaluation,
        {
            "analyse": "analyse",
            "report": "report",
        },
    )

    graph.add_edge("report", END)

    return graph.compile()
