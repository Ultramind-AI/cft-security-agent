from __future__ import annotations

import sys
from typing import Any

from agent.llm import ProviderFailoverClient, parse_route_specs
from agent.prompts import SYSTEM_PROMPT
from schemas.action import ActionProposal
from schemas.agent_outputs import AnalysisResult, ReevaluationResult
from schemas.hypothesis import Hypothesis
from schemas.llm import (
    LLMDockerfileUserActionChoice,
    LLMDynamicPlanChoice,
    LLMGeneralActionChoice,
    LLMPythonPasswordActionChoice,
    LLMReactHtmlFlowActionChoice,
)
from schemas.plan import DynamicPlan, PlannedAction
from schemas.state import AgentState


class FallbackLLMAgentModel:
    """Адаптер рассуждений LLM с резервированием за существующей границей AgentReasoningModel."""

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
        if _is_missing_user_finding(state):
            choice_model = LLMDockerfileUserActionChoice
        elif _is_unvalidated_password_finding(state):
            choice_model = LLMPythonPasswordActionChoice
        elif _is_react_dangerous_html_finding(state):
            choice_model = LLMReactHtmlFlowActionChoice
        else:
            choice_model = LLMGeneralActionChoice
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
            target=_target_profile(state).id,
            environment=_target_profile(state).environment,
            iteration=next_iteration,
            parameters=_sanitize_action_parameters(choice, state),
            purpose=choice.purpose,
            expected_evidence=choice.expected_evidence,
        )

    def build_plan(
        self,
        state: AgentState,
        analysis: AnalysisResult,
        hypothesis: Hypothesis,
    ) -> DynamicPlan:
        candidates = _allowed_plan_candidates(state)
        choice = self.client.complete_model(
            output_model=LLMDynamicPlanChoice,
            system_prompt=SYSTEM_PROMPT,
            user_payload={
                "task": (
                    "Build a bounded verification plan. You may either choose a deterministic "
                    "registered candidate or propose a sandbox_command argv. sandbox_command runs "
                    "only inside the disposable Docker lab with a read-only target mount, bounded "
                    "resources and no arbitrary network. Use registered runtime candidates for "
                    "network observations. Never invent target identity or sandbox session ids."
                ),
                "state": _reasoning_context(state),
                "analysis": analysis.model_dump(mode="json"),
                "hypothesis": hypothesis.model_dump(mode="json"),
                "registered_candidates": [
                    _public_candidate(candidate) for candidate in candidates
                ],
                "sandbox_command_contract": {
                    "kind": "sandbox_command",
                    "argv": "1-32 argv tokens; shell text is allowed only through an explicit argv such as sh -lc",
                    "cwd": ["/target", "/workspace"],
                    "network": "none; use registered runtime candidates for target HTTP",
                    "target_mount": "read-only at /target",
                    "workspace": "ephemeral writable /workspace",
                },
            },
            operation="build_dynamic_plan",
        )

        if len(choice.steps) > choice.max_steps:
            raise ValueError("LLM DynamicPlan contains more steps than max_steps")

        by_id = {str(candidate["candidate_id"]): candidate for candidate in candidates}
        selected_ids = [
            step.candidate_id
            for step in choice.steps
            if step.kind == "candidate" and step.candidate_id is not None
        ]
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("LLM DynamicPlan cannot repeat the same registered candidate")

        profile = _target_profile(state)
        runtime_services = state.get("runtime_services")
        first_iteration = int(state.get("iteration_count", 0)) + 1
        planned_steps: list[PlannedAction] = []
        for index, step in enumerate(choice.steps, start=1):
            iteration = first_iteration + index - 1
            if step.kind == "sandbox_command":
                action = ActionProposal(
                    id=_build_action_id(state["finding"].id, iteration),
                    tool="sandbox_command",
                    target=profile.id,
                    environment=profile.environment,
                    iteration=iteration,
                    parameters={"argv": list(step.argv), "cwd": step.cwd},
                    purpose=step.purpose or "Run a bounded command inside the disposable lab.",
                    expected_evidence=step.expected_observation,
                )
            else:
                candidate = by_id.get(str(step.candidate_id))
                if candidate is None:
                    raise ValueError(
                        f"LLM selected unknown DynamicPlan candidate: {step.candidate_id!r}"
                    )
                action = ActionProposal(
                    id=_build_action_id(state["finding"].id, iteration),
                    tool=str(candidate["tool"]),
                    target=profile.id,
                    environment=profile.environment,
                    iteration=iteration,
                    parameters=dict(candidate.get("parameters", {})),
                    purpose=str(candidate["purpose"]),
                    expected_evidence=step.expected_observation,
                    service=_optional_string(candidate.get("service")),
                    endpoint=_optional_string(candidate.get("endpoint")),
                )
            planned_steps.append(
                PlannedAction(
                    index=index,
                    action=action,
                    expected_observation=step.expected_observation,
                    continue_if=step.continue_if,
                )
            )

        return DynamicPlan(
            id=_build_plan_id(state["finding"].id, first_iteration),
            target=profile.id,
            environment=profile.environment,
            hypothesis_id=hypothesis.id,
            goal=choice.goal,
            max_steps=choice.max_steps,
            sandbox_session_id=(
                runtime_services.session_id if runtime_services is not None else None
            ),
            continuation_reason=choice.continuation_reason,
            stop_conditions=choice.stop_conditions,
            steps=planned_steps,
        )

    def reevaluate(self, state: AgentState) -> ReevaluationResult:
        deterministic = _evidence_guard(state)
        if deterministic is not None:
            if getattr(self.client, "trace", False):
                print(
                    "[agent] reevaluate: deterministic evidence guard "
                    f"-> {deterministic.status}",
                    file=sys.stderr,
                )
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

        # Последняя защитная проверка: вывод LLM не является Evidence
        if result.status in {"confirmed", "rejected"}:
            iteration_count = int(state.get("iteration_count", 0))
            max_iterations = int(state.get("max_steps", state.get("max_iterations", 2)))
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
    profile = _target_profile(state)
    runtime_services = state.get("runtime_services")
    return {
        "finding": finding.model_dump(mode="json"),
        "code_context": state.get("code_context"),
        "architecture_context": _dump_model(state.get("architecture_context")),
        "cvss": _dump_model(state.get("cvss")),
        "context_priority": _dump_model(state.get("context_priority")),
        "target_profile": {
            "id": profile.id,
            "environment": profile.environment,
            "services": {
                service_id: {
                    "type": service.type,
                    "runtime_endpoints": list(service.runtime_endpoints),
                }
                for service_id, service in profile.services.items()
            },
            "constraints": profile.constraints.model_dump(mode="json"),
        },
        "runtime_services": _dump_model(runtime_services),
        "sandbox_session_id": (
            runtime_services.session_id
            if runtime_services is not None
            else state.get("sandbox_session_id")
        ),
        "evidence": [item.model_dump(mode="json") for item in state.get("evidence", [])],
        "action_history": [
            item.model_dump(mode="json") for item in state.get("action_history", [])
        ],
        "decision_history": [
            item.model_dump(mode="json") for item in state.get("decision_history", [])
        ],
        "iteration_count": int(state.get("iteration_count", 0)),
        "max_steps": int(state.get("max_steps", state.get("max_iterations", 2))),
        "wall_clock_budget_seconds": float(state.get("wall_clock_budget_seconds", 120.0)),
        "started_at": _dump_model(state.get("started_at")),
        "stop_reason": state.get("stop_reason"),
    }


def _allowed_execution_tools(state: AgentState) -> list[dict[str, Any]]:
    if _is_missing_user_finding(state):
        return [
            {
                "name": "inspect_dockerfile_user",
                "parameters": _dockerfile_user_parameters(state),
                "purpose": (
                    "Read-only source verification of the final-stage USER directive "
                    "for the trusted Dockerfile artifact referenced by this finding."
                ),
            }
        ]

    if _is_unvalidated_password_finding(state):
        return [
            {
                "name": "inspect_python_password_assignment",
                "parameters": {
                    "artifact_id": _target_profile(state).artifact_id_for_path(
                        state["finding"].file,
                        kind="python",
                    )
                },
                "purpose": (
                    "Read-only Python AST verification of password assignment and "
                    "password-validation calls. Password literal values are never returned."
                ),
            }
        ]

    if _is_react_dangerous_html_finding(state):
        return [
            {
                "name": "inspect_react_dangerous_html_flow",
                "parameters": _react_html_flow_parameters(state),
                "purpose": (
                    "Bounded static source-flow verification from a writable user field "
                    "to dangerouslySetInnerHTML. No browser content is executed."
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


def _allowed_plan_candidates(state: AgentState) -> list[dict[str, Any]]:
    if (
        _is_missing_user_finding(state)
        or _is_unvalidated_password_finding(state)
        or _is_react_dangerous_html_finding(state)
    ):
        return [
            {
                "candidate_id": item["name"],
                "tool": item["name"],
                "parameters": dict(item.get("parameters", {})),
                "service": None,
                "endpoint": None,
                "purpose": item["purpose"],
            }
            for item in _allowed_execution_tools(state)
        ]

    runtime_services = state.get("runtime_services")
    profile = _target_profile(state)
    if runtime_services is not None:
        candidates: list[dict[str, Any]] = []
        for service_id in sorted(runtime_services.services):
            runtime_service = runtime_services.services[service_id]
            if service_id not in profile.services or not runtime_service.ready:
                continue
            for endpoint in sorted(set(runtime_service.allowed_endpoints)):
                candidates.append(
                    {
                        "candidate_id": f"runtime:{service_id}:{endpoint}",
                        "tool": "observe_http_surface",
                        "parameters": {},
                        "service": service_id,
                        "endpoint": endpoint,
                        "purpose": (
                            f"Observe the allowlisted endpoint {endpoint} for service "
                            f"{service_id} inside the current sandbox session."
                        ),
                    }
                )
        if candidates:
            return candidates

    candidates = []
    for service_id in sorted(profile.services):
        service = profile.services[service_id]
        for endpoint in sorted(set(service.runtime_endpoints)):
            candidates.append(
                {
                    "candidate_id": f"profile:{service_id}:{endpoint}",
                    "tool": "observe_http_surface",
                    "parameters": {},
                    "service": service_id,
                    "endpoint": endpoint,
                    "purpose": (
                        f"Observe the configured endpoint {endpoint} for service "
                        f"{service_id} once a trusted sandbox session is available."
                    ),
                }
            )
    return candidates


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "tool": candidate["tool"],
        "service": candidate.get("service"),
        "endpoint": candidate.get("endpoint"),
        "purpose": candidate["purpose"],
    }


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _is_missing_user_finding(state: AgentState) -> bool:
    return state["finding"].rule_id.lower().startswith("dockerfile.security.missing-user")


def _is_unvalidated_password_finding(state: AgentState) -> bool:
    return "unvalidated-password" in state["finding"].rule_id.lower()


def _is_react_dangerous_html_finding(state: AgentState) -> bool:
    return "react-dangerouslysetinnerhtml" in state["finding"].rule_id.lower()


def _dockerfile_user_parameters(state: AgentState) -> dict[str, str]:
    from agent.model import _dockerfile_user_parameters as parameters_for_path

    return parameters_for_path(state["finding"].file, _target_profile(state))


def _react_html_flow_parameters(state: AgentState) -> dict[str, str]:
    from agent.model import _react_html_flow_parameters as parameters

    return parameters(state["finding"].file, _target_profile(state))


def _sanitize_action_parameters(choice, state: AgentState) -> dict[str, object]:
    # Модель может выбрать только возможность из списка разрешений; чувствительные параметры
    # восстанавливаются из находки и таргета, а не принимаются из вывода модели
    if choice.tool == "inspect_dockerfile_user":
        return _dockerfile_user_parameters(state)
    if choice.tool == "inspect_python_password_assignment":
        return {
            "artifact_id": _target_profile(state).artifact_id_for_path(
                state["finding"].file,
                kind="python",
            )
        }
    if choice.tool == "inspect_react_dangerous_html_flow":
        return _react_html_flow_parameters(state)
    if choice.tool in {"check_sberlab_health", "get_sberlab_public_projects"}:
        return {}
    raise ValueError(f"No deterministic parameter contract for tool: {choice.tool}")



def _target_profile(state: AgentState):
    from agent.model import _target_profile as profile_for_state

    return profile_for_state(state)

def _evidence_guard(state: AgentState) -> ReevaluationResult | None:
    action = state.get("proposed_action")
    execution = state.get("execution")
    if action is None or execution is None:
        return None

    iteration_count = int(state.get("iteration_count", 0))
    max_iterations = int(state.get("max_steps", state.get("max_iterations", 2)))

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
    # Терминальный verdict допускается только от Evidence этой ActionProposal
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
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _build_plan_id(finding_id: str, iteration: int) -> str:
    from agent.model import _build_plan_id as build_plan_id

    return build_plan_id(finding_id, iteration)


def _build_action_id(finding_id: str, iteration: int) -> str:
    # Локальный импорт не связывает LLM-транспорт с деталями детерминированной модели
    from agent.model import _build_action_id as build_action_id

    return build_action_id(finding_id, iteration)
