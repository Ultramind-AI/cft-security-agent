import json
from pathlib import Path

from agent.model import DeterministicAgentModel
from evidence.interpreter import build_evidence
from executor import worker
from schemas.action import ActionProposal
from schemas.architecture import ArchitectureContext
from schemas.evidence import Evidence
from schemas.execution import ExecutionResult
from schemas.finding import Finding
from validator.validator import PolicyValidator


def _real_missing_user_state() -> dict:
    return {
        "finding": Finding(
            id="dockerfile.security.missing-user.missing-user:backend/Dockerfile:14",
            source="semgrep",
            rule_id="dockerfile.security.missing-user.missing-user",
            title="Dockerfile missing USER",
            description="Container image does not explicitly set USER.",
            file="backend/Dockerfile",
            line_start=14,
            line_end=14,
            severity="ERROR",
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
        "max_iterations": 1,
        "evidence": [],
    }


def _write_dockerfile(root: Path, text: str) -> Path:
    dockerfile = root / "backend" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text(text, encoding="utf-8")
    return dockerfile


def test_agent_selects_fixed_dockerfile_user_capability() -> None:
    model = DeterministicAgentModel()
    state = _real_missing_user_state()

    analysis = model.analyse(state)
    hypothesis = model.form_hypothesis(state, analysis)
    proposal = model.propose_action(state, analysis, hypothesis)

    assert proposal.tool == "check_sberlab_backend_dockerfile_user"
    assert proposal.parameters == {}
    assert proposal.target == "sberlab-local"
    assert "Dockerfile" in proposal.expected_evidence

    validation = PolicyValidator.from_yaml(
        "policies/default.yaml",
        target_file="targets/sberlab.yaml",
    ).validate(proposal)
    assert validation.approved is True


def test_worker_confirms_missing_user_source_condition(tmp_path) -> None:
    _write_dockerfile(
        tmp_path,
        "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nCMD [\"python\"]\n",
    )

    exit_code, stdout, stderr = worker._execute(
        {
            "tool": "check_sberlab_backend_dockerfile_user",
            "repository_path": str(tmp_path),
            "parameters": {},
            "request_timeout_seconds": 1,
            "max_output_bytes": 4096,
        }
    )

    payload = json.loads(stdout)
    assert exit_code == 0
    assert stderr == ""
    assert payload["verdict"] == "confirmed"
    assert payload["user_directive_present"] is False
    assert payload["runtime_user_verified"] is False
    assert payload["scope"] == "source"


def test_worker_rejects_missing_user_condition_when_final_stage_sets_user(tmp_path) -> None:
    _write_dockerfile(
        tmp_path,
        (
            "FROM python:3.11-slim AS build\n"
            "USER builder\n"
            "FROM python:3.11-slim\n"
            "WORKDIR /app\n"
            "USER appuser\n"
        ),
    )

    exit_code, stdout, stderr = worker._execute(
        {
            "tool": "check_sberlab_backend_dockerfile_user",
            "repository_path": str(tmp_path),
            "parameters": {},
            "request_timeout_seconds": 1,
            "max_output_bytes": 4096,
        }
    )

    payload = json.loads(stdout)
    assert exit_code == 0
    assert stderr == ""
    assert payload["verdict"] == "rejected"
    assert payload["user_directive_present"] is True
    assert payload["user"] == "appuser"
    assert payload["user_line"] == 5
    assert payload["final_stage"] == 2


def test_worker_is_inconclusive_when_trusted_repository_is_missing() -> None:
    exit_code, stdout, stderr = worker._execute(
        {
            "tool": "check_sberlab_backend_dockerfile_user",
            "repository_path": "",
            "parameters": {},
            "request_timeout_seconds": 1,
            "max_output_bytes": 4096,
        }
    )

    assert exit_code == 1
    assert stdout == ""
    assert "repository path is not configured" in stderr


def test_interpreter_turns_structured_worker_output_into_verdict_evidence() -> None:
    action = ActionProposal(
        id="action-docker-user-check",
        tool="check_sberlab_backend_dockerfile_user",
        target="sberlab-local",
        purpose="Verify fixed Dockerfile condition.",
        expected_evidence="Structured source evidence.",
    )
    execution = ExecutionResult(
        run_id="run-docker-user-check",
        action_id=action.id,
        status="completed",
        exit_code=0,
        evidence_ref="execution-run-docker-user-check",
        audit_ref="audit:run-docker-user-check",
    )
    record = {
        "run_id": execution.run_id,
        "action_id": action.id,
        "status": "completed",
        "exit_code": 0,
        "stdout": json.dumps(
            {
                "schema": "cft.dockerfile_user_check.v1",
                "dockerfile": "backend/Dockerfile",
                "final_stage": 1,
                "user_directive_present": False,
                "user": None,
                "user_line": None,
                "verdict": "confirmed",
                "scope": "source",
                "runtime_user_verified": False,
                "explanation": "Final stage has no explicit USER directive.",
            }
        ),
    }

    evidence = build_evidence(
        action=action,
        execution=execution,
        record=record,
        evidence_loaded=True,
        artifact_refs=["artifact.json"],
    )

    assert evidence.type == "dockerfile_user_check"
    assert evidence.verdict == "confirmed"
    assert evidence.reliability == "high"
    assert evidence.details["runtime_user_verified"] is False


def test_model_uses_capability_evidence_verdict_not_execution_success() -> None:
    model = DeterministicAgentModel()
    state = _real_missing_user_state()
    state["iteration_count"] = 1
    state["proposed_action"] = ActionProposal(
        id="action-docker-user-check",
        tool="check_sberlab_backend_dockerfile_user",
        target="sberlab-local",
        purpose="Verify fixed Dockerfile condition.",
        expected_evidence="Structured source evidence.",
    )
    state["execution"] = ExecutionResult(
        run_id="run-docker-user-check",
        action_id="action-docker-user-check",
        status="completed",
        exit_code=0,
        evidence_ref="execution-run-docker-user-check",
        audit_ref="audit:run-docker-user-check",
    )
    state["evidence"] = [
        Evidence(
            id="evidence-docker-user-check",
            action_id="action-docker-user-check",
            type="dockerfile_user_check",
            summary="Source condition is present.",
            reliability="high",
            verdict="confirmed",
            details={"runtime_user_verified": False},
        )
    ]

    result = model.reevaluate(state)

    assert result.status == "confirmed"
    assert "structured Evidence" in result.explanation


def test_model_keeps_non_verdict_execution_inconclusive() -> None:
    model = DeterministicAgentModel()
    state = _real_missing_user_state()
    state["iteration_count"] = 1
    state["proposed_action"] = ActionProposal(
        id="action-health",
        tool="check_sberlab_health",
        target="sberlab-local",
        purpose="Check health only.",
        expected_evidence="Health response.",
    )
    state["execution"] = ExecutionResult(
        run_id="run-health",
        action_id="action-health",
        status="completed",
        exit_code=0,
        evidence_ref="execution-run-health",
        audit_ref="audit:run-health",
    )
    state["evidence"] = [
        Evidence(
            id="evidence-health",
            action_id="action-health",
            type="test_result",
            summary="Health request completed.",
            reliability="high",
        )
    ]

    result = model.reevaluate(state)

    assert result.status == "inconclusive"
