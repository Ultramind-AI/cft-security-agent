import re
from typing import Protocol

from app.config import settings
from schemas.action import ActionProposal
from schemas.agent_outputs import AnalysisResult, ReevaluationResult
from schemas.hypothesis import Hypothesis
from schemas.state import AgentState


class AgentReasoningModel(Protocol):
    """
    Граница между узлами LangGraph и реализацией рассуждений.

    Будущий настоящий адаптер LLM должен реализовывать именно этот интерфейс и
    возвращать существующие структурированные схемы проекта.
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
    Тестовая реализация до подключения настоящего провайдера LLM.

    Она не делает внешних вызовов и сохраняет детерминированность интеграционных тестов.
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
        elif _is_missing_user_finding(finding.rule_id):
            tool = "inspect_dockerfile_user"
            parameters = _dockerfile_user_parameters(finding.file)
            purpose = (
                "Inspect the trusted Dockerfile artifact for the effective final-stage "
                "USER directive using a reusable read-only source capability."
            )
            expected_evidence = (
                "Structured source evidence classifying the final Dockerfile USER as "
                "missing, root, non-root or dynamic."
            )
        elif _is_unvalidated_password_finding(finding.rule_id):
            tool = "inspect_python_password_assignment"
            parameters = {"artifact_id": "demo_seed"}
            purpose = (
                "Inspect the trusted Python seed artifact for password assignment and "
                "Django password-validation calls without exposing password values."
            )
            expected_evidence = (
                "Structured source evidence with password assignment/validation counts "
                "and redacted hardcoded-password metadata."
            )
        elif _is_react_dangerous_html_finding(finding.rule_id):
            tool = "inspect_react_dangerous_html_flow"
            parameters = _react_html_flow_parameters()
            purpose = (
                "Perform a bounded static source-flow check from the writable user field "
                "to the React dangerous HTML sink without executing browser content."
            )
            expected_evidence = (
                "Structured source-flow evidence covering sink, sanitizer, serializer "
                "writability and API update-route facts."
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
            # Для реальных возможностей успех запуска еще не является вердиктом
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
    Центральная фабрика моделей.

    Узлы LangGraph зависят только от AgentReasoningModel. После подключения настоящего
    провайдера адаптер можно добавить сюда без переписывания топологии графа.
    """
    if settings.agent_mode == "stub":
        return DeterministicAgentModel()

    if settings.agent_mode == "llm":
        from agent.llm_model import FallbackLLMAgentModel

        return FallbackLLMAgentModel.from_settings(settings)

    raise RuntimeError(f"Unsupported agent mode: {settings.agent_mode}")


def _build_action_id(finding_id: str, iteration: int) -> str:
    """Создать безопасный для валидатора понятный идентификатор действия из произвольного идентификатора находки."""
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "-", finding_id).strip("-")
    normalized = normalized or "finding"
    # Оставляем место для префикса действия, суффикса итерации и разделителей
    normalized = normalized[:96]
    return f"action-{normalized}-{iteration}"


def _requested_test_outcome(state: AgentState) -> str:
    severity = (state["finding"].severity or "").upper()

    mapping = {
        "TEST_CONFIRMED": "confirmed",
        "TEST_REJECTED": "rejected",
        "TEST_INCONCLUSIVE": "inconclusive",
    }

    # Серьезность реального SAST не является вердиктом проверки; только синтетические TEST_*
    # значения серьезности могут управлять тестовым safe_noop
    return mapping.get(severity, "inconclusive")


def _is_missing_user_finding(rule_id: str) -> bool:
    return rule_id.lower().startswith("dockerfile.security.missing-user")


def _is_unvalidated_password_finding(rule_id: str) -> bool:
    return "unvalidated-password" in rule_id.lower()


def _is_react_dangerous_html_finding(rule_id: str) -> bool:
    return "react-dangerouslysetinnerhtml" in rule_id.lower()


def _normalize_finding_path(path: str) -> str:
    return path.replace("\\", "/")


def _dockerfile_user_parameters(file_path: str) -> dict[str, str]:
    normalized = _normalize_finding_path(file_path)
    mapping = {
        "backend/Dockerfile": "backend_dockerfile",
        "frontend/frontend/Dockerfile": "frontend_dockerfile",
    }
    try:
        artifact_id = mapping[normalized]
    except KeyError as exc:
        raise ValueError(
            f"No trusted Dockerfile artifact mapping for finding path: {normalized}"
        ) from exc
    return {"artifact_id": artifact_id}


def _react_html_flow_parameters() -> dict[str, str]:
    return {
        "frontend_artifact_id": "frontend_app",
        "model_artifact_id": "user_model",
        "serializer_artifact_id": "user_serializer",
        "view_artifact_id": "user_views",
        "field": "about",
    }
