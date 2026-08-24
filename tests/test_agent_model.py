
from agent.model import DeterministicAgentModel
from agent.prompts import SYSTEM_PROMPT
from schemas.action import ActionProposal
from schemas.architecture import ArchitectureContext
from schemas.execution import ExecutionResult
from schemas.finding import Finding
from validator.validator import PolicyValidator


def _state() -> dict:
    return {
        "finding": Finding(
            id="model-test-001",
            source="semgrep",
            rule_id="test.rule",
            title="Synthetic model test",
            description="Deterministic test only.",
            file="backend/example.py",
            severity="TEST_CONFIRMED",
            service="backend",
        ),
        "architecture_context": ArchitectureContext(
            service="backend",
            public_exposure=True,
            criticality="high",
            connected_services=["database"],
            databases=["database"],
        ),
        "iteration_count": 0,
        "max_iterations": 2,
        "evidence": [],
    }


def test_deterministic_model_returns_structured_analysis() -> None:
    model = DeterministicAgentModel()
    result = model.analyse(_state())

    assert result.needs_verification is True
    assert "public_exposure" in result.risk_signals
    assert "database_connectivity" in result.risk_signals


def test_deterministic_model_proposes_registered_stub_action() -> None:
    model = DeterministicAgentModel()
    state = _state()

    analysis = model.analyse(state)
    hypothesis = model.form_hypothesis(state, analysis)
    proposal = model.propose_action(
        state,
        analysis,
        hypothesis,
    )

    assert proposal.tool == "safe_noop"
    assert proposal.target == "sberlab-local"
    assert "shell" not in proposal.parameters


def test_system_prompt_contains_security_boundaries() -> None:
    required_phrases = [
        "Never execute actions directly.",
        "Never bypass Validator.",
        "Raw commands are allowed only through sandbox_command inside the disposable Docker lab.",
        "Never mark a finding confirmed without Evidence.",
    ]

    for phrase in required_phrases:
        assert phrase in SYSTEM_PROMPT


def test_real_sast_severity_does_not_become_fake_confirmation() -> None:
    model = DeterministicAgentModel()
    state = _state()
    state["finding"] = state["finding"].model_copy(update={"severity": "ERROR"})

    analysis = model.analyse(state)
    hypothesis = model.form_hypothesis(state, analysis)
    proposal = model.propose_action(state, analysis, hypothesis)

    assert proposal.parameters["test_outcome"] == "inconclusive"


def test_successful_non_stub_execution_is_not_a_verdict() -> None:
    model = DeterministicAgentModel()
    state = _state()
    state["iteration_count"] = 1
    state["max_iterations"] = 1
    state["proposed_action"] = ActionProposal(
        id="action-real-capability",
        tool="observe_http_surface",
        service="backend",
        endpoint="/health/",
        target="sberlab-local",
        purpose="Controlled capability semantics test.",
        expected_evidence="Structured result.",
    )
    state["execution"] = ExecutionResult(
        run_id="run-real-capability",
        action_id="action-real-capability",
        status="completed",
        exit_code=0,
        evidence_ref="execution-test",
        audit_ref="audit:test",
    )

    result = model.reevaluate(state)

    assert result.status == "inconclusive"
    assert "execution" in result.explanation.lower()
    assert "verdict" in result.explanation.lower() or "hypothesis" in result.explanation.lower()


def test_real_finding_id_produces_validator_safe_action_id() -> None:
    model = DeterministicAgentModel()
    state = _state()
    state["finding"] = state["finding"].model_copy(
        update={
            "id": "docker.rule:backend/Dockerfile:14",
            "severity": "ERROR",
        }
    )

    analysis = model.analyse(state)
    hypothesis = model.form_hypothesis(state, analysis)
    proposal = model.propose_action(state, analysis, hypothesis)

    assert "/" not in proposal.id
    assert proposal.id.startswith("action-docker.rule:backend-Dockerfile:14-")

    validation = PolicyValidator.from_yaml(
        "policies/default.yaml",
        target_file="targets/sberlab.yaml",
    ).validate(proposal)
    assert validation.approved is True
