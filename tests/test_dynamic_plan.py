from __future__ import annotations

from agent.llm_model import FallbackLLMAgentModel
from agent.model import DeterministicAgentModel
from agent.planning import DynamicPlanValidator
from schemas.action import ActionProposal
from schemas.agent_outputs import AnalysisResult
from schemas.finding import Finding
from schemas.hypothesis import Hypothesis
from schemas.llm import LLMDynamicPlanChoice, LLMPlanStepChoice
from schemas.plan import DynamicPlan, PlannedAction
from schemas.runtime import RuntimeService, RuntimeServiceMap
from schemas.target import TargetProfile, TargetService


class _FakeClient:
    def __init__(self, output: LLMDynamicPlanChoice) -> None:
        self.output = output
        self.calls: list[dict] = []

    def complete_model(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["output_model"] is LLMDynamicPlanChoice
        return self.output


def _profile() -> TargetProfile:
    return TargetProfile(
        id="demo-target",
        environment="sandbox",
        services={
            "api": TargetService(
                id="api",
                type="web",
                runtime_endpoints=["/health/", "/api/items/"],
            )
        },
    )


def _runtime_map() -> RuntimeServiceMap:
    return RuntimeServiceMap(
        session_id="session-current",
        network_name="cft-sandbox-demo",
        services={
            "api": RuntimeService(
                name="api",
                type="web",
                address="http://api:8000",
                ready=True,
                readiness_source="compose_health",
                allowed_endpoints=["/health/", "/api/items/"],
            )
        },
    )


def _finding() -> Finding:
    return Finding(
        id="generic.rule:src/app.py:10",
        source="semgrep",
        rule_id="generic.rule",
        title="Generic finding",
        description="Needs controlled runtime verification.",
        file="src/app.py",
        line_start=10,
        severity="WARNING",
        service="api",
    )


def _hypothesis() -> Hypothesis:
    return Hypothesis(
        id="hypothesis-demo",
        statement="The exposed route may have a security-relevant behavior.",
        based_on=["finding"],
        expected_evidence="HTTP surface observations",
        confidence=0.6,
    )


def _state(*, max_iterations: int = 3) -> dict:
    return {
        "target_profile": _profile(),
        "runtime_services": _runtime_map(),
        "finding": _finding(),
        "hypothesis": _hypothesis(),
        "iteration_count": 0,
        "max_iterations": max_iterations,
        "evidence": [],
    }


def _runtime_action(*, endpoint: str = "/health/", target: str = "demo-target") -> ActionProposal:
    return ActionProposal(
        id="action-generic.rule:src-app.py:10-1",
        tool="observe_http_surface",
        target=target,
        environment="sandbox",
        iteration=1,
        parameters={},
        purpose="Observe one allowlisted endpoint in the active sandbox.",
        expected_evidence="HTTP metadata without response body.",
        service="api",
        endpoint=endpoint,
    )


def _plan(*, action: ActionProposal | None = None, session_id: str = "session-current") -> DynamicPlan:
    action = action or _runtime_action()
    return DynamicPlan(
        id="plan-demo-1",
        target=action.target,
        environment=action.environment,
        hypothesis_id="hypothesis-demo",
        goal="Verify the hypothesis with bounded runtime observations.",
        max_steps=1,
        sandbox_session_id=session_id,
        continuation_reason="Continue only if the first observation is insufficient.",
        stop_conditions=["terminal_evidence", "step_limit_reached"],
        steps=[
            PlannedAction(
                index=1,
                action=action,
                expected_observation="HTTP metadata without response body.",
                continue_if="The observation is inconclusive.",
            )
        ],
    )


def test_dynamic_plan_validator_accepts_current_runtime_scope() -> None:
    result = DynamicPlanValidator().validate(_plan(), _state())

    assert result.approved is True
    assert "sandbox_session_matches_runtime_map" in result.rules
    assert "all_actions_within_runtime_scope" in result.rules


def test_dynamic_plan_validator_rejects_target_outside_profile() -> None:
    result = DynamicPlanValidator().validate(
        _plan(action=_runtime_action(target="other-target")),
        _state(),
    )

    assert result.approved is False
    assert "target" in result.reason.lower()


def test_dynamic_plan_validator_rejects_endpoint_outside_runtime_map() -> None:
    result = DynamicPlanValidator().validate(
        _plan(action=_runtime_action(endpoint="/admin/internal")),
        _state(),
    )

    assert result.approved is False
    assert "not allowed" in result.reason


def test_dynamic_plan_validator_rejects_stale_sandbox_session() -> None:
    result = DynamicPlanValidator().validate(
        _plan(session_id="session-stale"),
        _state(),
    )

    assert result.approved is False
    assert "sandbox session" in result.reason.lower()


def test_dynamic_plan_validator_enforces_remaining_iteration_budget() -> None:
    action = _runtime_action()
    second = action.model_copy(
        update={
            "id": "action-generic.rule:src-app.py:10-2",
            "iteration": 2,
            "endpoint": "/api/items/",
        }
    )
    plan = DynamicPlan(
        id="plan-two-steps",
        target="demo-target",
        environment="sandbox",
        hypothesis_id="hypothesis-demo",
        goal="Use two observations.",
        max_steps=2,
        sandbox_session_id="session-current",
        continuation_reason="A second observation may be required.",
        steps=[
            PlannedAction(
                index=1,
                action=action,
                expected_observation="First observation",
                continue_if="First observation is inconclusive",
            ),
            PlannedAction(
                index=2,
                action=second,
                expected_observation="Second observation",
                continue_if="No more steps are available",
            ),
        ],
    )

    result = DynamicPlanValidator().validate(plan, _state(max_iterations=1))

    assert result.approved is False
    assert "iteration budget" in result.reason


def test_deterministic_model_wraps_existing_action_in_dynamic_plan() -> None:
    state = _state()
    state.pop("runtime_services")
    state["finding"] = state["finding"].model_copy(update={"severity": "TEST_CONFIRMED"})
    model = DeterministicAgentModel()
    analysis = model.analyse(state)
    hypothesis = model.form_hypothesis(state, analysis)
    state["hypothesis"] = hypothesis

    plan = model.build_plan(state, analysis, hypothesis)

    assert plan.target == "demo-target"
    assert plan.hypothesis_id == hypothesis.id
    assert plan.max_steps == 1
    assert plan.steps[0].action.tool == "safe_noop"
    assert plan.steps[0].action.iteration == 1


def test_llm_builds_multistep_plan_only_from_runtime_candidates() -> None:
    choice = LLMDynamicPlanChoice(
        goal="Compare health and application route observations.",
        max_steps=2,
        continuation_reason="Use the second route only when health evidence is insufficient.",
        stop_conditions=["terminal_evidence", "step_limit_reached"],
        steps=[
            LLMPlanStepChoice(
                candidate_id="runtime:api:/health/",
                expected_observation="Health endpoint response metadata.",
                continue_if="Health metadata does not resolve the hypothesis.",
            ),
            LLMPlanStepChoice(
                candidate_id="runtime:api:/api/items/",
                expected_observation="Application endpoint response metadata.",
                continue_if="No terminal evidence is available after this step.",
            ),
        ],
    )
    client = _FakeClient(choice)
    model = FallbackLLMAgentModel(client)
    state = _state()
    analysis = AnalysisResult(summary="Runtime verification is required.")
    hypothesis = state["hypothesis"]

    plan = model.build_plan(state, analysis, hypothesis)

    assert [step.action.tool for step in plan.steps] == [
        "observe_http_surface",
        "observe_http_surface",
    ]
    assert [step.action.endpoint for step in plan.steps] == ["/health/", "/api/items/"]
    assert [step.action.iteration for step in plan.steps] == [1, 2]
    assert all(step.action.target == "demo-target" for step in plan.steps)
    assert all(step.action.environment == "sandbox" for step in plan.steps)
    assert all(step.action.service == "api" for step in plan.steps)
    assert plan.sandbox_session_id == "session-current"
    assert DynamicPlanValidator().validate(plan, state).approved is True
    assert client.calls[0]["operation"] == "build_dynamic_plan"


def test_llm_invented_runtime_candidate_uses_deterministic_plan() -> None:
    choice = LLMDynamicPlanChoice(
        goal="Try an out-of-scope route.",
        max_steps=1,
        continuation_reason="No continuation.",
        steps=[
            LLMPlanStepChoice(
                candidate_id="runtime:api:/admin/internal",
                expected_observation="Out-of-scope data.",
                continue_if="Never.",
            )
        ],
    )
    model = FallbackLLMAgentModel(_FakeClient(choice))
    state = _state()

    plan = model.build_plan(
        state,
        AnalysisResult(summary="test"),
        state["hypothesis"],
    )

    assert plan.steps[0].action.tool == "safe_noop"
    assert plan.steps[0].action.service is None


def test_plan_node_persists_scope_validation_before_policy(monkeypatch) -> None:
    from agent import nodes

    state = _state()
    state.pop("runtime_services")
    state["finding"] = state["finding"].model_copy(update={"severity": "TEST_CONFIRMED"})
    model = DeterministicAgentModel()
    analysis = model.analyse(state)
    hypothesis = model.form_hypothesis(state, analysis)
    state["analysis"] = analysis
    state["hypothesis"] = hypothesis
    monkeypatch.setattr(nodes, "get_agent_model", lambda: model)

    planned = nodes.propose_action(state)
    merged = {**state, **planned}
    validated = nodes.validate_action(merged)

    assert planned["dynamic_plan"].steps[0].action == planned["proposed_action"]
    assert planned["plan_validation"].approved is True
    assert validated["validation"].approved is True


def test_invalid_plan_scope_is_blocked_before_policy_validator(monkeypatch) -> None:
    from agent import nodes

    state = _state()
    bad_plan = _plan(action=_runtime_action(endpoint="/admin/internal"))

    class _BadPlanModel:
        def build_plan(self, state, analysis, hypothesis):
            return bad_plan

    state["analysis"] = AnalysisResult(summary="test")
    monkeypatch.setattr(nodes, "get_agent_model", lambda: _BadPlanModel())

    planned = nodes.propose_action(state)
    validated = nodes.validate_action({**state, **planned})

    assert planned["plan_validation"].approved is False
    assert validated["validation"].approved is False
    assert validated["status"] == "policy_blocked"
    assert "not allowed" in validated["validation"].reason


def test_llm_can_plan_generic_networkless_sandbox_command() -> None:
    choice = LLMDynamicPlanChoice(
        goal="Inspect repository configuration before choosing a runtime observation.",
        max_steps=1,
        continuation_reason="Replan after reading the command Evidence.",
        stop_conditions=["terminal_evidence", "step_limit_reached"],
        steps=[
            LLMPlanStepChoice(
                kind="sandbox_command",
                argv=["python", "-c", "print('inspect-settings')"],
                cwd="/target",
                purpose="Inspect target files inside the disposable security lab.",
                expected_observation="A bounded command observation.",
                continue_if="The observation is not terminal Evidence.",
            )
        ],
    )
    client = _FakeClient(choice)
    model = FallbackLLMAgentModel(client)
    state = _state()

    plan = model.build_plan(
        state,
        AnalysisResult(summary="Repository inspection is required."),
        state["hypothesis"],
    )

    action = plan.steps[0].action
    assert action.tool == "sandbox_command"
    assert action.parameters == {
        "argv": ["python", "-c", "print('inspect-settings')"],
        "cwd": "/target",
    }
    assert action.service is None
    assert action.endpoint is None
    assert DynamicPlanValidator().validate(plan, state).approved is True
    contract = client.calls[0]["user_payload"]["sandbox_command_contract"]
    assert contract["network"].startswith("none")


def test_dynamic_plan_rejects_sandbox_command_with_runtime_scope() -> None:
    action = ActionProposal(
        id="sandbox-command-1",
        tool="sandbox_command",
        target="demo-target",
        environment="sandbox",
        iteration=1,
        parameters={"argv": ["python", "-V"], "cwd": "/target"},
        purpose="Inspect the repository inside the disposable lab.",
        expected_evidence="Bounded command output.",
        service="api",
        endpoint="/health/",
    )
    plan = _plan(action=action)

    result = DynamicPlanValidator().validate(plan, _state())

    assert result.approved is False
    assert "cannot request runtime network scope" in result.reason
