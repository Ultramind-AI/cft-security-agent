from __future__ import annotations

from typing import Any

from agent.llm import ProviderFailoverClient, parse_route_specs
from agent.prompts import SYSTEM_PROMPT
from schemas.action import ActionProposal
from schemas.agent_outputs import AnalysisResult, ReevaluationResult
from schemas.hypothesis import Hypothesis
from schemas.llm import LLMDockerfileUserActionChoice, LLMGeneralActionChoice
from schemas.state import AgentState


class FallbackLLMAgentModel:
    """Real LLM reasoning adapter behind the existing AgentReasoningModel boundary."""

    def __init__(self, client: ProviderFailoverClient) -> None:
        self.client = client

    @classmethod
    def from_settings(cls, settings) -> FallbackLLMAgentModel:
        return cls(
            ProviderFailoverClient(
                routes=parse_route_specs(settings.llm_routes),
                credentials=settings.llm_provider_credentials(),
                timeout_seconds=settings.llm_timeout_seconds,
                max_output_tokens=settings.llm_max_output_tokens,
                trace=settings.llm_trace,
            )
        )

    def analyse(self, state: AgentState) -> AnalysisResult:
        return self.client.complete_model(
            output_model=AnalysisResult,
            system_prompt=SYSTEM_PROMPT,
            user_payload={
                "task": "Analyse the finding. Separate facts from assumptions.",
                "state": _reasoning_context(state),
            },
            operation="analyse",
        )

    def form_hypothesis(
        self,
        state: AgentState,
        analysis: AnalysisResult,
    ) -> Hypothesis:
        return self.client.complete_model(
            output_model=Hypothesis,
            system_prompt=SYSTEM_PROMPT,
            user_payload={
                "task": "Form one testable defensive verification hypothesis.",
                "state": _reasoning_context(state),
                "analysis": analysis.model_dump(mode="json"),
            },
            operation="form_hypothesis",
        )

    def propose_action(
        self,
        state: AgentState,
        analysis: AnalysisResult,
        hypothesis: Hypothesis,
    ) -> ActionProposal:
        allowed_tools = _allowed_execution_tools(state)
        choice_model = (
            LLMDockerfileUserActionChoice
            if _is_backend_missing_user_finding(state)
            else LLMGeneralActionChoice
        )
        choice = self.client.complete_model(
            output_model=choice_model,
            system_prompt=SYSTEM_PROMPT,
            user_payload={
                "task": (
                    "Choose exactly one registered verification capability. "
                    "Do not invent tools, paths, URLs, shell commands or target scope."
                ),
                "state": _reasoning_context(state),
                "analysis": analysis.model_dump(mode="json"),
                "hypothesis": hypothesis.model_dump(mode="json"),
                "allowed_tools": allowed_tools,
            },
            operation="propose_action",
        )

        allowed_names = {item["name"] for item in allowed_tools}
        if choice.tool not in allowed_names:
            raise ValueError(
                f"LLM selected tool {choice.tool!r}, which is not allowed for this finding"
            )

        finding = state["finding"]
        next_iteration = int(state.get("iteration_count", 0)) + 1
        return ActionProposal(
            id=_build_action_id(finding.id, next_iteration),
            tool=choice.tool,
            target="sberlab-local",
            environment="local",
            iteration=next_iteration,
            parameters=_sanitize_action_parameters(choice),
            purpose=choice.purpose,
            expected_evidence=choice.expected_evidence,
        )

    def reevaluate(self, state: AgentState) -> ReevaluationResult:
        deterministic = _evidence_guard(state)
        if deterministic is not None:
            return deterministic

        result = self.client.complete_model(
            output_model=ReevaluationResult,
            system_prompt=SYSTEM_PROMPT,
            user_payload={
                "task": (
                    "Re-evaluate the hypothesis using only supplied Evidence. "
                    "Never infer confirmed or rejected from execution success alone."
                ),
                "state": _reasoning_context(state),
            },
            operation="reevaluate",
        )

        # Final safety guard: an LLM conclusion is not Evidence.
        if result.status in {"confirmed", "rejected"}:
            iteration_count = int(state.get("iteration_count", 0))
            max_iterations = int(state.get("max_iterations", 2))
            if iteration_count >= max_iterations:
                return ReevaluationResult(
                    status="inconclusive",
                    explanation=(
                        "The LLM proposed a terminal verdict without matching structured "
                        "Evidence; the iteration limit was reached."
                    ),
                )
            return ReevaluationResult(
                status="continue",
                explanation=(
                    "The LLM proposed a terminal verdict without matching structured "
                    "Evidence; another controlled iteration is required."
                ),
            )
        return result


def _reasoning_context(state: AgentState) -> dict[str, Any]:
    finding = state["finding"]
    return {
        "finding": finding.model_dump(mode="json"),
        "code_context": state.get("code_context"),
        "architecture_context": _dump_model(state.get("architecture_context")),
        "cvss": _dump_model(state.get("cvss")),
        "context_priority": _dump_model(state.get("context_priority")),
        "evidence": [item.model_dump(mode="json") for item in state.get("evidence", [])],
        "iteration_count": int(state.get("iteration_count", 0)),
        "max_iterations": int(state.get("max_iterations", 2)),
    }


def _allowed_execution_tools(state: AgentState) -> list[dict[str, Any]]:
    if _is_backend_missing_user_finding(state):
        return [
            {
                "name": "check_sberlab_backend_dockerfile_user",
                "parameters": {},
                "purpose": (
                    "Read-only source verification of whether the final backend Dockerfile "
                    "stage contains a USER directive."
                ),
            }
        ]

    return [
        {
            "name": "check_sberlab_health",
            "parameters": {},
            "purpose": "Fixed GET /health/ against the configured local SberLab target.",
        },
        {
            "name": "get_sberlab_public_projects",
            "parameters": {},
            "purpose": "Fixed GET /api/projects/ against the configured local SberLab target.",
        },
    ]



def _is_backend_missing_user_finding(state: AgentState) -> bool:
    finding = state["finding"]
    return (
        finding.service == "backend"
        and finding.rule_id.lower().startswith("dockerfile.security.missing-user")
    )


def _sanitize_action_parameters(choice) -> dict[str, object]:
    # Every currently exposed live-LLM capability has an empty parameter contract.
    # Ignore model-supplied parameters instead of letting them cross the policy boundary.
    if choice.tool in {
        "check_sberlab_health",
        "get_sberlab_public_projects",
        "check_sberlab_backend_dockerfile_user",
    }:
        return {}
    raise ValueError(f"No deterministic parameter contract for tool: {choice.tool}")

def _evidence_guard(state: AgentState) -> ReevaluationResult | None:
    action = state.get("proposed_action")
    execution = state.get("execution")
    if action is None or execution is None:
        return None

    iteration_count = int(state.get("iteration_count", 0))
    max_iterations = int(state.get("max_iterations", 2))

    if execution.status != "completed" or execution.exit_code != 0:
        if iteration_count >= max_iterations:
            return ReevaluationResult(
                status="inconclusive",
                explanation="Approved verification did not complete successfully.",
            )
        return None

    matching = [
        item
        for item in state.get("evidence", [])
        if item.action_id == action.id and item.verdict is not None
    ]
    if matching:
        verdict = matching[-1].verdict
        if verdict in {"confirmed", "rejected"}:
            return ReevaluationResult(
                status=verdict,
                explanation=(
                    "Capability-specific structured Evidence established the finding "
                    f"verdict: {verdict}."
                ),
            )

    if iteration_count >= max_iterations:
        return ReevaluationResult(
            status="inconclusive",
            explanation=(
                "The iteration limit was reached without capability-specific Evidence "
                "for a confirmed or rejected verdict."
            ),
        )
    return None


def _dump_model(value: Any) -> Any:
    return value.model_dump(mode="json") if value is not None else None


def _build_action_id(finding_id: str, iteration: int) -> str:
    # Import here avoids making the LLM transport depend on deterministic model internals.
    from agent.model import _build_action_id as build_action_id

    return build_action_id(finding_id, iteration)
