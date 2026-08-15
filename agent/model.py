from typing import Protocol

from app.config import settings
from schemas.action import ActionProposal
from schemas.agent_outputs import AnalysisResult, ReevaluationResult
from schemas.hypothesis import Hypothesis
from schemas.state import AgentState


class AgentReasoningModel(Protocol):
    """
    Boundary between LangGraph nodes and the reasoning implementation.

    A future real LLM adapter should implement exactly this interface and return
    the existing structured project schemas.
    """

    def analyse(self, state: AgentState) -> AnalysisResult: ...

    def form_hypothesis(
        self,
        state: AgentState,
        analysis: AnalysisResult,
    ) -> Hypothesis: ...

    def propose_action(
        self,
        state: AgentState,
        analysis: AnalysisResult,
        hypothesis: Hypothesis,
    ) -> ActionProposal: ...

    def reevaluate(self, state: AgentState) -> ReevaluationResult: ...


class DeterministicAgentModel:
    """
    Test implementation used until a real LLM provider is connected.

    It contains no external calls and keeps integration tests deterministic.
    """

    def analyse(self, state: AgentState) -> AnalysisResult:
        finding = state["finding"]
        context = state.get("architecture_context")

        risk_signals: list[str] = [
            f"source:{finding.source}",
            f"rule:{finding.rule_id}",
        ]

        if context is not None:
            if context.public_exposure:
                risk_signals.append("public_exposure")
            if context.databases:
                risk_signals.append("database_connectivity")
            if context.criticality.lower() in {"high", "critical"}:
                risk_signals.append(
                    f"criticality:{context.criticality.lower()}"
                )

        return AnalysisResult(
            summary=(
                f"Finding '{finding.title}' requires controlled verification "
                "before a final conclusion."
            ),
            risk_signals=risk_signals,
            needs_verification=True,
        )

    def form_hypothesis(
        self,
        state: AgentState,
        analysis: AnalysisResult,
    ) -> Hypothesis:
        finding = state["finding"]

        return Hypothesis(
            statement=(
                f"The finding '{finding.title}' may be valid and requires "
                "controlled verification."
            ),
            based_on=[
                analysis.summary,
                *analysis.risk_signals,
                f"file:{finding.file}",
            ],
            expected_evidence=(
                "A structured result from an approved safe verification action."
            ),
            confidence=0.5,
        )

    def propose_action(
        self,
        state: AgentState,
        analysis: AnalysisResult,
        hypothesis: Hypothesis,
    ) -> ActionProposal:
        finding = state["finding"]
        next_iteration = int(state.get("iteration_count", 0)) + 1

        # Test-only branch used to verify that Validator can block a proposal.
        tool = (
            "unknown_tool"
            if finding.service == "force-deny"
            else "safe_noop"
        )

        return ActionProposal(
            id=f"action-{finding.id}-{next_iteration}",
            tool=tool,
            target="sberlab-local",
            parameters={
                "message": f"verify:{finding.id}:iteration:{next_iteration}",
                "test_outcome": _requested_test_outcome(state),
            },
            purpose="Run one predefined safe verification stub.",
            expected_evidence=hypothesis.expected_evidence,
        )

    def reevaluate(self, state: AgentState) -> ReevaluationResult:
        outcome = str(
            state["proposed_action"].parameters.get(
                "test_outcome",
                "confirmed",
            )
        )

        if outcome == "confirmed":
            return ReevaluationResult(
                status="confirmed",
                explanation="Controlled evidence confirmed the hypothesis.",
            )

        if outcome == "rejected":
            return ReevaluationResult(
                status="rejected",
                explanation="Controlled evidence rejected the hypothesis.",
            )

        iteration_count = int(state.get("iteration_count", 0))
        max_iterations = int(state.get("max_iterations", 2))

        if iteration_count >= max_iterations:
            return ReevaluationResult(
                status="inconclusive",
                explanation=(
                    "Evidence remained insufficient until the iteration limit."
                ),
            )

        return ReevaluationResult(
            status="continue",
            explanation="More controlled evidence is required.",
        )


def get_agent_model() -> AgentReasoningModel:
    """
    Central model factory.

    LangGraph nodes depend only on AgentReasoningModel. When the team connects a
    real provider, add an adapter here without rewriting graph topology.
    """
    if settings.agent_mode == "stub":
        return DeterministicAgentModel()

    if settings.agent_mode == "llm":
        raise RuntimeError(
            "CFT_AGENT_MODE=llm is selected, but no real LLM adapter is "
            "connected yet. Implement AgentReasoningModel and register it "
            "inside get_agent_model()."
        )

    raise RuntimeError(f"Unsupported agent mode: {settings.agent_mode}")


def _requested_test_outcome(state: AgentState) -> str:
    severity = (state["finding"].severity or "").upper()

    mapping = {
        "TEST_CONFIRMED": "confirmed",
        "TEST_REJECTED": "rejected",
        "TEST_INCONCLUSIVE": "inconclusive",
    }

    return mapping.get(severity, "confirmed")
