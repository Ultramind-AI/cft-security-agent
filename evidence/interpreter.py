import json
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from schemas.action import ActionProposal
from schemas.evidence import (
    Evidence,
    EvidenceAction,
    EvidenceArtifact,
    EvidenceObservation,
    EvidenceScope,
)
from schemas.execution import ExecutionResult
from schemas.security_tools import (
    DockerfileUserCheckResult,
    PythonPasswordAssignmentCheckResult,
    ReactDangerousHtmlFlowCheckResult,
)

DOCKERFILE_USER_TOOL = "inspect_dockerfile_user"
PYTHON_PASSWORD_TOOL = "inspect_python_password_assignment"
REACT_HTML_FLOW_TOOL = "inspect_react_dangerous_html_flow"


_SPECIALIZED_RESULTS: dict[str, tuple[type[BaseModel], str]] = {
    DOCKERFILE_USER_TOOL: (DockerfileUserCheckResult, "dockerfile_user_check"),
    PYTHON_PASSWORD_TOOL: (
        PythonPasswordAssignmentCheckResult,
        "python_password_assignment_check",
    ),
    REACT_HTML_FLOW_TOOL: (
        ReactDangerousHtmlFlowCheckResult,
        "react_dangerous_html_flow_check",
    ),
}


def build_evidence(
    *,
    action: ActionProposal,
    execution: ExecutionResult,
    record: dict,
    evidence_loaded: bool,
    artifact_refs: list[str],
    hypothesis_id: str,
    sandbox_session_id: str | None = None,
) -> Evidence:
    """Преобразует сохраненный результат Executor в Evidence"""
    specialized = _SPECIALIZED_RESULTS.get(action.tool)
    if specialized is not None:
        evidence = _structured_capability_evidence(
            action=action,
            execution=execution,
            record=record,
            evidence_loaded=evidence_loaded,
            artifact_refs=artifact_refs,
            hypothesis_id=hypothesis_id,
            sandbox_session_id=sandbox_session_id,
            result_model=specialized[0],
            evidence_type=specialized[1],
        )
        if evidence is not None:
            return evidence

    record_status = str(record["status"]) if evidence_loaded else execution.status
    record_exit_code = int(record["exit_code"]) if evidence_loaded else execution.exit_code
    read_status = "read from persistent storage" if evidence_loaded else "unavailable"

    return _evidence(
        action=action,
        execution=execution,
        evidence_type="test_result",
        summary=(
            f"Executor evidence {execution.evidence_ref} {read_status}: "
            f"executor_status={record_status}, exit_code={record_exit_code}, "
            f"timed_out={execution.timed_out}."
        ),
        facts={
            "executor_status": record_status,
            "exit_code": record_exit_code,
            "timed_out": execution.timed_out,
            "record_loaded": evidence_loaded,
        },
        scope_description="bounded executor result; not a vulnerability verdict",
        artifact_refs=artifact_refs,
        reliability="high" if evidence_loaded and record_status == "completed" else "low",
        hypothesis_id=hypothesis_id,
        sandbox_session_id=sandbox_session_id,
    )


def _structured_capability_evidence(
    *,
    action: ActionProposal,
    execution: ExecutionResult,
    record: dict,
    evidence_loaded: bool,
    artifact_refs: list[str],
    hypothesis_id: str,
    sandbox_session_id: str | None,
    result_model: type[BaseModel],
    evidence_type: str,
) -> Evidence | None:
    if not evidence_loaded:
        return None
    if str(record.get("status")) != "completed" or int(record.get("exit_code", 1)) != 0:
        return None

    try:
        payload = result_model.model_validate_json(str(record.get("stdout", "")))
    except (ValidationError, json.JSONDecodeError, TypeError, ValueError):
        return _evidence(
            action=action,
            execution=execution,
            evidence_type=evidence_type,
            summary="Executor completed, but capability-specific evidence was malformed.",
            facts={"schema_valid": False},
            scope_description="bounded capability result could not be parsed",
            artifact_refs=artifact_refs,
            reliability="low",
            verdict="inconclusive",
            hypothesis_id=hypothesis_id,
            sandbox_session_id=sandbox_session_id,
        )

    data = payload.model_dump(by_alias=True)
    verdict = data.get("verdict")
    explanation = str(data.get("explanation", "Capability verification completed."))
    scope_description = str(data.get("scope", "bounded capability result"))
    facts = {
        key: value
        for key, value in data.items()
        if key not in {"verdict", "explanation", "scope"}
    }
    return _evidence(
        action=action,
        execution=execution,
        evidence_type=evidence_type,
        summary=explanation,
        facts=facts,
        scope_description=scope_description,
        artifact_refs=artifact_refs,
        reliability="high",
        verdict=verdict,
        hypothesis_id=hypothesis_id,
        sandbox_session_id=sandbox_session_id,
    )


def _evidence(
    *,
    action: ActionProposal,
    execution: ExecutionResult,
    evidence_type: str,
    summary: str,
    facts: dict[str, object],
    scope_description: str,
    artifact_refs: list[str],
    reliability: str,
    hypothesis_id: str,
    sandbox_session_id: str | None,
    verdict: str | None = None,
) -> Evidence:
    artifacts = [_artifact(ref) for ref in artifact_refs]
    return Evidence(
        id=f"evidence-{uuid4().hex[:10]}",
        action_id=action.id,
        type=evidence_type,
        summary=summary,
        artifact_refs=artifact_refs,
        reliability=reliability,
        verdict=verdict,
        source="runtime" if sandbox_session_id is not None else "static",
        sandbox_session_id=sandbox_session_id,
        hypothesis_id=hypothesis_id,
        action=EvidenceAction(id=action.id, tool=action.tool, run_id=execution.run_id),
        observation=EvidenceObservation(kind=evidence_type, facts=facts),
        scope=EvidenceScope(
            target=action.target,
            environment=action.environment,
            service=action.service,
            description=scope_description,
        ),
        artifacts=artifacts,
    )


def _artifact(reference: str) -> EvidenceArtifact:
    if reference.startswith("execution-"):
        role = "execution"
    elif reference.startswith("audit:"):
        role = "audit"
    else:
        role = "other"
    return EvidenceArtifact(ref=reference, role=role)
