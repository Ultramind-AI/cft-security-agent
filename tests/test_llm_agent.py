from agent.llm_model import FallbackLLMAgentModel
from schemas.action import ActionProposal
from schemas.agent_outputs import AnalysisResult, ReevaluationResult
from schemas.architecture import ArchitectureContext
from schemas.evidence import (
    Evidence,
    EvidenceAction,
    EvidenceObservation,
    EvidenceScope,
)
from schemas.execution import ExecutionResult
from schemas.finding import Finding
from schemas.hypothesis import Hypothesis
from schemas.llm import LLMDockerfileUserActionChoice


class _FakeClient:
    def __init__(self, outputs, *, trace: bool = False):
        self.outputs = iter(outputs)
        self.calls = []
        self.trace = trace

    def complete_model(self, **kwargs):
        self.calls.append(kwargs)
        output = next(self.outputs)
        assert isinstance(output, kwargs["output_model"])
        return output


def _state() -> dict:
    return {
        "finding": Finding(
            id="docker.rule:backend/Dockerfile:14",
            source="semgrep",
            rule_id="dockerfile.security.missing-user.missing-user",
            title="Missing USER",
            description="Container may use the default user.",
            file="backend/Dockerfile",
            line_start=14,
            severity="ERROR",
            service="backend",
        ),
        "code_context": "FROM python:3.13\nCMD [\"python\", \"app.py\"]",
        "architecture_context": ArchitectureContext(
            service="backend",
            public_exposure=True,
            criticality="high",
        ),
        "evidence": [],
        "iteration_count": 0,
        "max_iterations": 1,
    }


def test_llm_model_returns_structured_reasoning() -> None:
    client = _FakeClient(
        [AnalysisResult(summary="Needs verification", risk_signals=["container"])]
    )
    model = FallbackLLMAgentModel(client)
    result = model.analyse(_state())
    assert result.summary == "Needs verification"
    assert client.calls[0]["operation"] == "analyse"


def test_llm_action_keeps_security_sensitive_fields_deterministic() -> None:
    choice = LLMDockerfileUserActionChoice(
        tool="inspect_dockerfile_user",
        parameters={"path": "../../should-never-be-used"},
        purpose="Verify the Dockerfile user directive.",
        expected_evidence="Structured Dockerfile user evidence.",
    )
    model = FallbackLLMAgentModel(_FakeClient([choice]))
    state = _state()
    analysis = AnalysisResult(summary="Needs verification")
    hypothesis = Hypothesis(
        statement="The final stage has no USER directive.",
        based_on=["finding"],
        expected_evidence="Dockerfile evidence",
        confidence=0.8,
    )

    action = model.propose_action(state, analysis, hypothesis)

    assert action.tool == "inspect_dockerfile_user"
    assert action.target == "sberlab-local"
    assert action.environment == "local"
    assert action.iteration == 1
    assert action.parameters == {"artifact_id": "backend_dockerfile"}
    assert "/" not in action.id


def test_llm_cannot_confirm_without_matching_evidence() -> None:
    model = FallbackLLMAgentModel(
        _FakeClient(
            [
                ReevaluationResult(
                    status="confirmed",
                    explanation="Model tried to confirm without evidence.",
                )
            ]
        )
    )
    state = _state()
    state["iteration_count"] = 1
    state["proposed_action"] = ActionProposal(
        id="action-1",
        tool="inspect_dockerfile_user",
        target="sberlab-local",
        purpose="test",
        expected_evidence="test",
    )
    state["execution"] = ExecutionResult(
        run_id="run-1",
        action_id="action-1",
        status="completed",
        exit_code=0,
        evidence_ref="ev-1",
        audit_ref="audit-1",
    )

    result = model.reevaluate(state)

    assert result.status == "inconclusive"
    assert "capability-specific Evidence" in result.explanation


def test_capability_evidence_deterministically_controls_llm_verdict() -> None:
    model = FallbackLLMAgentModel(_FakeClient([]))
    state = _state()
    state["iteration_count"] = 1
    state["proposed_action"] = ActionProposal(
        id="action-1",
        tool="inspect_dockerfile_user",
        target="sberlab-local",
        purpose="test",
        expected_evidence="test",
    )
    state["execution"] = ExecutionResult(
        run_id="run-1",
        action_id="action-1",
        status="completed",
        exit_code=0,
        evidence_ref="ev-1",
        audit_ref="audit-1",
    )
    state["evidence"] = [
        Evidence(
            id="ev-1",
            action_id="action-1",
            type="dockerfile_user_check",
            summary="Final stage has no USER directive.",
            reliability="high",
            verdict="confirmed",
            source="static",
            hypothesis_id="hypothesis-1",
            action=EvidenceAction(
                id="action-1",
                tool="inspect_dockerfile_user",
                run_id="run-1",
            ),
            observation=EvidenceObservation(
                kind="dockerfile_user_check",
                facts={"user_directive_present": False},
            ),
            scope=EvidenceScope(
                target="sberlab-local",
                environment="local",
                description="source-only",
            ),
        )
    ]

    result = model.reevaluate(state)

    assert result.status == "confirmed"
    assert "structured Evidence" in result.explanation


def test_evidence_guard_trace_makes_short_circuit_visible(capsys) -> None:
    client = _FakeClient([], trace=True)
    model = FallbackLLMAgentModel(client)
    state = _state()
    state["iteration_count"] = 1
    state["proposed_action"] = ActionProposal(
        id="action-trace",
        tool="inspect_dockerfile_user",
        target="sberlab-local",
        purpose="test",
        expected_evidence="test",
    )
    state["execution"] = ExecutionResult(
        run_id="run-trace",
        action_id="action-trace",
        status="completed",
        exit_code=0,
        evidence_ref="ev-trace",
        audit_ref="audit-trace",
    )
    state["evidence"] = [
        Evidence(
            id="ev-trace",
            action_id="action-trace",
            type="dockerfile_user_check",
            summary="Final stage has no USER directive.",
            reliability="high",
            verdict="confirmed",
            source="static",
            hypothesis_id="hypothesis-trace",
            action=EvidenceAction(
                id="action-trace",
                tool="inspect_dockerfile_user",
                run_id="run-trace",
            ),
            observation=EvidenceObservation(
                kind="dockerfile_user_check",
                facts={"user_directive_present": False},
            ),
            scope=EvidenceScope(
                target="sberlab-local",
                environment="local",
                description="source-only",
            ),
        )
    ]

    result = model.reevaluate(state)
    captured = capsys.readouterr()

    assert result.status == "confirmed"
    assert client.calls == []
    assert (
        "[agent] reevaluate: deterministic evidence guard -> confirmed"
        in captured.err
    )
