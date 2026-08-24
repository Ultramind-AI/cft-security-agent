from __future__ import annotations

import pytest

from schemas.evidence import (
    Evidence,
    EvidenceAction,
    EvidenceArtifact,
    EvidenceObservation,
    EvidenceScope,
)
from schemas.hypothesis import Hypothesis


def _evidence(*, source: str = "static", sandbox_session_id: str | None = None) -> Evidence:
    return Evidence(
        id="evidence-1",
        action_id="action-1",
        type="http_status_observation",
        summary="The target returned HTTP 200.",
        reliability="high",
        source=source,
        sandbox_session_id=sandbox_session_id,
        hypothesis_id="hypothesis-1",
        action=EvidenceAction(
            id="action-1",
            tool="observe_http_response",
            run_id="run-1",
        ),
        observation=EvidenceObservation(
            kind="http_status_observation",
            facts={"status_code": 200, "response_body_stored": False},
        ),
        scope=EvidenceScope(
            target="target-1",
            environment="sandbox",
            service="backend",
            description="GET /health/ on the registered backend service",
        ),
        artifact_refs=["execution-run-1", "audit:run-1"],
        artifacts=[
            EvidenceArtifact(ref="execution-run-1", role="execution"),
            EvidenceArtifact(ref="audit:run-1", role="audit"),
        ],
    )


def test_runtime_evidence_requires_a_sandbox_session_id() -> None:
    with pytest.raises(ValueError, match="Runtime Evidence requires sandbox_session_id"):
        _evidence(source="runtime")


def test_runtime_evidence_carries_observation_separately_from_human_summary() -> None:
    evidence = _evidence(source="runtime", sandbox_session_id="session-1")

    assert evidence.observation.facts == {
        "status_code": 200,
        "response_body_stored": False,
    }
    assert evidence.summary == "The target returned HTTP 200."
    assert evidence.verdict is None
    with pytest.raises((TypeError, ValueError)):
        evidence.summary = "LLM replacement"  # type: ignore[misc]


def test_evidence_rejects_mismatched_legacy_and_provenance_fields() -> None:
    payload = _evidence().model_dump()
    payload["action_id"] = "another-action"

    with pytest.raises(ValueError, match="Evidence action_id must match action.id"):
        Evidence.model_validate(payload)


def test_hypothesis_has_a_run_scoped_identity_for_evidence_links() -> None:
    hypothesis = Hypothesis(
        statement="The registered health endpoint is reachable.",
        expected_evidence="A bounded HTTP status observation.",
        confidence=0.5,
    )

    assert hypothesis.id.startswith("hypothesis-")


def test_sandbox_command_evidence_preserves_bounded_observation_without_verdict() -> None:
    from evidence.interpreter import build_evidence
    from schemas.action import ActionProposal
    from schemas.execution import ExecutionResult

    action = ActionProposal(
        id="sandbox-action-1",
        tool="sandbox_command",
        target="target-1",
        environment="sandbox",
        parameters={
            "argv": ["python", "-c", "print('token=must-not-be-retained')"],
            "cwd": "/target",
        },
        purpose="Inspect repository state inside the disposable lab.",
        expected_evidence="Bounded command output.",
    )
    execution = ExecutionResult(
        run_id="run-sandbox-1",
        action_id=action.id,
        status="completed",
        exit_code=0,
        stdout="setting=true",
        stderr="",
        workspace_id="run-sandbox-1-workspace",
        evidence_ref="execution-sandbox-1",
        audit_ref="audit:run-sandbox-1",
    )
    evidence = build_evidence(
        action=action,
        execution=execution,
        record={
            "status": "completed",
            "exit_code": 0,
            "stdout": "setting=true",
            "stderr": "",
        },
        evidence_loaded=True,
        artifact_refs=[],
        hypothesis_id="hypothesis-1",
        sandbox_session_id="run-sandbox-1-workspace",
    )

    assert evidence.source == "runtime"
    assert evidence.verdict is None
    assert evidence.observation.facts["stdout"] == "setting=true"
    assert "must-not-be-retained" not in str(evidence.observation.facts["argv"])
    assert evidence.scope.description.startswith("disposable sandbox command output")
