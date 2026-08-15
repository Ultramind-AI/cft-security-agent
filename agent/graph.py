"""
Orchestration skeleton.

Recommended real implementation:
- LangChain for model/tool structured interfaces.
- LangGraph for AgentState + nodes + conditional edges + cycles.

START -> load_context -> score_finding -> analyse -> form_hypothesis
-> propose_action -> validate_action
   DENY -> policy_blocked -> report -> END
   APPROVE -> execute -> collect_evidence -> reevaluate
      stop -> report -> END
      continue -> analyse
"""

def build_graph() -> None:
    """Replace with a real LangGraph StateGraph after contracts stabilize."""
    return None
