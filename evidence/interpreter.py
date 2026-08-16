import json
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from schemas.action import ActionProposal
from schemas.evidence import Evidence
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
) -> Evidence:
    """Convert persisted Executor output into capability-aware Evidence."""
    specialized = _SPECIALIZED_RESULTS.get(action.tool)
    if specialized is not None:
        evidence = _structured_capability_evidence(
            action=action,
            execution=execution,
            record=record,
            evidence_loaded=evidence_loaded,
            artifact_refs=artifact_refs,
            result_model=specialized[0],
            evidence_type=specialized[1],
        )
        if evidence is not None:
            return evidence

    record_status = str(record["status"]) if evidence_loaded else execution.status
    record_exit_code = int(record["exit_code"]) if evidence_loaded else execution.exit_code
    read_status = "read from persistent storage" if evidence_loaded else "unavailable"

    return Evidence(
        id=f"evidence-{uuid4().hex[:10]}",
        action_id=action.id,
        type="test_result",
        summary=(
            f"Executor evidence {execution.evidence_ref} {read_status}: "
            f"executor_status={record_status}, exit_code={record_exit_code}, "
            f"timed_out={execution.timed_out}."
        ),
        artifact_refs=artifact_refs,
        reliability=(
            "high" if evidence_loaded and record_status == "completed" else "low"
        ),
    )


def _structured_capability_evidence(
    *,
    action: ActionProposal,
    execution: ExecutionResult,
    record: dict,
    evidence_loaded: bool,
    artifact_refs: list[str],
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
        return Evidence(
            id=f"evidence-{uuid4().hex[:10]}",
            action_id=action.id,
            type=evidence_type,
            summary="Executor completed, but capability-specific evidence was malformed.",
            artifact_refs=artifact_refs,
            reliability="low",
            verdict="inconclusive",
            details={"schema_valid": False},
        )

    data = payload.model_dump(by_alias=True)
    verdict = data.get("verdict")
    explanation = str(data.get("explanation", "Capability verification completed."))
    return Evidence(
        id=f"evidence-{uuid4().hex[:10]}",
        action_id=action.id,
        type=evidence_type,
        summary=explanation,
        artifact_refs=artifact_refs,
        reliability="high",
        verdict=verdict,
        details=data,
    )
