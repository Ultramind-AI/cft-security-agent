from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    analyse,
    build_report,
    collect_evidence,
    execute_action,
    form_hypothesis,
    guard_agent_budget,
    load_context,
    propose_action,
    reevaluate,
    score_finding,
    validate_action,
)
from schemas.state import AgentState

# T15 has several LangGraph super-steps per agent iteration; keep LangGraph
# recursion guard above the application-level max_steps budget.
_AGENT_GRAPH_RECURSION_LIMIT = 128


def _after_validation(state: AgentState) -> str:
    if state["validation"].approved:
        return "execute"
    # Отказ валидатора завершает ветку: обход политики не предусмотрен
    return "report"


def _after_budget_guard(state: AgentState) -> str:
    return "analyse" if state.get("status") == "budget_ok" else "report"


def _after_reevaluation(state: AgentState) -> str:
    if state.get("status") == "continue":
        # Новая итерация заново анализирует накопленный Evidence и строит свежий план.
        return "guard_agent_budget"
    return "report"


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("load_context", load_context)
    graph.add_node("score_finding", score_finding)
    graph.add_node("guard_agent_budget", guard_agent_budget)
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
    graph.add_edge("score_finding", "guard_agent_budget")
    graph.add_conditional_edges(
        "guard_agent_budget",
        _after_budget_guard,
        {
            "analyse": "analyse",
            "report": "report",
        },
    )
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
            "guard_agent_budget": "guard_agent_budget",
            "report": "report",
        },
    )

    graph.add_edge("report", END)

    compiled = graph.compile()
    compiled.config = {"recursion_limit": _AGENT_GRAPH_RECURSION_LIMIT}
    return compiled
