
from agent.model import DeterministicAgentModel
from agent.prompts import SYSTEM_PROMPT
from schemas.architecture import ArchitectureContext
from schemas.finding import Finding


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
        "Never request arbitrary shell commands.",
        "Never mark a finding confirmed without Evidence.",
    ]

    for phrase in required_phrases:
        assert phrase in SYSTEM_PROMPT
