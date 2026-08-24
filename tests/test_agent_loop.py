from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent import nodes
from agent.loop import apply_budget_to_reevaluation, wall_clock_exhausted
from schemas.action import ActionProposal
from schemas.agent_outputs import AnalysisResult, ReevaluationResult
from schemas.evidence import Evidence, EvidenceAction, EvidenceObservation, EvidenceScope
from schemas.finding import Finding
from schemas.hypothesis import Hypothesis
from schemas.plan import DynamicPlan, PlannedAction
from schemas.target import TargetProfile


def _profile() -> TargetProfile:
    return TargetProfile(id="loop-target", environment="sandbox")


def _finding() -> Finding:
    return Finding(
        id="loop-finding",
        source="semgrep",
        rule_id="generic.loop",
        title="Adaptive loop finding",
        description="Synthetic adaptive-loop test finding.",
        file="app.py",
        severity="WARNING",
    )


def _evidence(action: ActionProposal, stdout: str) -> Evidence:
    return Evidence(
        id=f"evidence-{action.iteration}",
        action_id=action.id,
        type="sandbox_command_observation",
        summary="Bounded sandbox command observation.",
        reliability="high",
        source="static",
        hypothesis_id=f"hypothesis-{action.iteration}",
        action=EvidenceAction(id=action.id, tool=action.tool, run_id=f"run-{action.iteration}"),
        observation=EvidenceObservation(
            kind="sandbox_command_observation",
            facts={"stdout": stdout, "exit_code": 0},
        ),
        scope=EvidenceScope(
            target=action.target,
            environment=action.environment,
            description="networkless disposable sandbox",
        ),
    )


class _AdaptiveModel:
    def analyse(self, state):
        observations = [item.observation.facts.get("stdout") for item in state.get("evidence", [])]
        return AnalysisResult(summary=f"observations={observations}")

    def form_hypothesis(self, state, analysis):
        iteration = int(state.get("iteration_count", 0)) + 1
        return Hypothesis(
            id=f"hypothesis-{iteration}",
            statement="Use the previous observation to choose the next repository inspection.",
            based_on=[analysis.summary],
            expected_evidence="Bounded sandbox command output.",
            confidence=0.5,
        )

    def build_plan(self, state, analysis, hypothesis):
        seen_first = any(
            item.observation.facts.get("stdout") == "first-observation"
            for item in state.get("evidence", [])
        )
        iteration = int(state.get("iteration_count", 0)) + 1
        argv = ["python", "-c", "print('second')"] if seen_first else ["python", "-c", "print('first')"]
        action = ActionProposal(
            id=f"action-{iteration}",
            tool="sandbox_command",
            target="loop-target",
            environment="sandbox",
            iteration=iteration,
            parameters={"argv": argv, "cwd": "/target"},
            purpose="Choose the next inspection from accumulated Evidence.",
            expected_evidence="Bounded command output.",
        )
        return DynamicPlan(
            id=f"plan-{iteration}",
            target="loop-target",
            environment="sandbox",
            hypothesis_id=hypothesis.id,
            goal="Adapt the next action to the previous Evidence.",
            max_steps=1,
            continuation_reason="Replan after every observation.",
            steps=[
                PlannedAction(
                    index=1,
                    action=action,
                    expected_observation="Bounded command output.",
                    continue_if="Evidence is not terminal.",
                )
            ],
        )

    def reevaluate(self, state):
        return ReevaluationResult(
            status="continue",
            explanation="Use the new Evidence to select another action.",
        )


def test_second_action_is_replanned_from_first_evidence(monkeypatch) -> None:
    model = _AdaptiveModel()
    monkeypatch.setattr(nodes, "get_agent_model", lambda: model)
    state = {
        "target_profile": _profile(),
        "finding": _finding(),
        "evidence": [],
        "iteration_count": 0,
        "max_iterations": 3,
        "max_steps": 3,
    }
    state.update(nodes.load_context(state))

    state.update(nodes.analyse(state))
    state.update(nodes.form_hypothesis(state))
    state.update(nodes.propose_action(state))
    first = state["proposed_action"]
    assert first.parameters["argv"][-1] == "print('first')"

    state["evidence"] = [_evidence(first, "first-observation")]
    reevaluated = nodes.reevaluate(state)
    state.update(reevaluated)
    assert state["status"] == "continue"

    state.update(nodes.guard_agent_budget(state))
    state.update(nodes.analyse(state))
    state.update(nodes.form_hypothesis(state))
    state.update(nodes.propose_action(state))
    second = state["proposed_action"]

    assert second.iteration == 2
    assert second.parameters["argv"][-1] == "print('second')"
    assert first.id != second.id
    assert len(state["plan_history"]) == 2
    assert state["decision_history"][-1].evidence_ids == ["evidence-1"]


def test_step_budget_turns_continue_into_inconclusive() -> None:
    state = {"iteration_count": 2, "max_steps": 2}
    result, reason = apply_budget_to_reevaluation(
        state,
        ReevaluationResult(status="continue", explanation="Keep investigating."),
    )

    assert result.status == "inconclusive"
    assert reason == "step_budget_exhausted"


def test_wall_clock_budget_is_enforced_before_next_reasoning_iteration() -> None:
    state = {
        "finding": _finding(),
        "target_profile": _profile(),
        "iteration_count": 1,
        "max_steps": 3,
        "started_at": datetime.now(UTC) - timedelta(seconds=10),
        "wall_clock_budget_seconds": 1.0,
        "evidence": [],
    }

    assert wall_clock_exhausted(state) is True
    guarded = nodes.guard_agent_budget(state)
    assert guarded["status"] == "inconclusive"
    assert guarded["stop_reason"] == "wall_clock_budget_exhausted"
    assert guarded["decision_history"][-1].outcome == "stop"
