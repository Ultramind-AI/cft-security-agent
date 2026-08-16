import re
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

        if finding.service == "force-deny":
            tool = "unknown_tool"
            parameters = {}
            purpose = "Exercise the deterministic Validator deny path."
            expected_evidence = hypothesis.expected_evidence
        elif _is_backend_missing_user_finding(finding.rule_id, finding.service):
            tool = "check_sberlab_backend_dockerfile_user"
            parameters = {}
            purpose = (
                "Verify whether the final backend Dockerfile stage explicitly "
                "sets a USER directive using a fixed read-only source check."
            )
            expected_evidence = (
                "Structured source evidence describing whether backend/Dockerfile "
                "contains a USER directive in its final build stage."
            )
        else:
            tool = "safe_noop"
            parameters = {
                "message": f"verify:{finding.id}:iteration:{next_iteration}",
                "test_outcome": _requested_test_outcome(state),
            }
            purpose = "Run one predefined safe verification stub."
            expected_evidence = hypothesis.expected_evidence

        return ActionProposal(
            id=_build_action_id(finding.id, next_iteration),
            tool=tool,
            target="sberlab-local",
            environment="local",
            iteration=next_iteration,
            parameters=parameters,
            purpose=purpose,
            expected_evidence=expected_evidence,
        )

    def reevaluate(self, state: AgentState) -> ReevaluationResult:
        execution = state["execution"]
        iteration_count = int(state.get("iteration_count", 0))
        max_iterations = int(state.get("max_iterations", 2))

        if execution.status != "completed" or execution.exit_code != 0:
            if iteration_count >= max_iterations:
                return ReevaluationResult(
                    status="inconclusive",
                    explanation="Approved verification did not complete successfully.",
                )
            return ReevaluationResult(
                status="continue",
                explanation="Execution failed; another controlled iteration is allowed.",
            )

        if state["proposed_action"].tool != "safe_noop":
            current_action_id = state["proposed_action"].id
            capability_evidence = [
                item
                for item in state.get("evidence", [])
                if item.action_id == current_action_id and item.verdict is not None
            ]
            if capability_evidence:
                verdict = capability_evidence[-1].verdict
                if verdict in {"confirmed", "rejected"}:
                    return ReevaluationResult(
                        status=verdict,
                        explanation=(
                            "Capability-specific structured Evidence established "
                            f"the finding verdict: {verdict}."
                        ),
                    )

            if iteration_count >= max_iterations:
                return ReevaluationResult(
                    status="inconclusive",
                    explanation=(
                        "Execution completed, but capability-specific Evidence did not "
                        "establish a confirmed or rejected verdict."
                    ),
                )
            return ReevaluationResult(
                status="continue",
                explanation=(
                    "Execution completed, but execution success is not a vulnerability "
                    "verdict. More controlled Evidence is required."
                ),
            )

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
        from agent.llm_model import FallbackLLMAgentModel

        return FallbackLLMAgentModel.from_settings(settings)

    raise RuntimeError(f"Unsupported agent mode: {settings.agent_mode}")


def _build_action_id(finding_id: str, iteration: int) -> str:
    """Build a Validator-safe, human-readable action id from an arbitrary finding id."""
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "-", finding_id).strip("-")
    normalized = normalized or "finding"
    # Keep room for the action prefix, iteration suffix and separators.
    normalized = normalized[:96]
    return f"action-{normalized}-{iteration}"


def _requested_test_outcome(state: AgentState) -> str:
    severity = (state["finding"].severity or "").upper()

    mapping = {
        "TEST_CONFIRMED": "confirmed",
        "TEST_REJECTED": "rejected",
        "TEST_INCONCLUSIVE": "inconclusive",
    }

    # Real SAST severity is not a verification verdict. Only synthetic TEST_*
    # severities may drive the safe_noop integration fixture.
    return mapping.get(severity, "inconclusive")


def _is_backend_missing_user_finding(rule_id: str, service: str | None) -> bool:
    return (
        service == "backend"
        and rule_id.lower().startswith("dockerfile.security.missing-user")
    )
