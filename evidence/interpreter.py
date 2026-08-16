import json
from uuid import uuid4

from pydantic import ValidationError

from schemas.action import ActionProposal
from schemas.evidence import Evidence
from schemas.execution import ExecutionResult
from schemas.security_tools import DockerfileUserCheckResult

DOCKERFILE_USER_TOOL = "check_sberlab_backend_dockerfile_user"


def build_evidence(
    *,
    action: ActionProposal,
    execution: ExecutionResult,
    record: dict,
    evidence_loaded: bool,
    artifact_refs: list[str],
) -> Evidence:
    """Convert persisted Executor output into capability-aware Evidence."""
    if action.tool == DOCKERFILE_USER_TOOL:
        specialized = _dockerfile_user_evidence(
            action=action,
            execution=execution,
            record=record,
            evidence_loaded=evidence_loaded,
            artifact_refs=artifact_refs,
        )
        if specialized is not None:
            return specialized

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


def _dockerfile_user_evidence(
    *,
    action: ActionProposal,
    execution: ExecutionResult,
    record: dict,
    evidence_loaded: bool,
    artifact_refs: list[str],
) -> Evidence | None:
    if not evidence_loaded:
        return None
    if str(record.get("status")) != "completed" or int(record.get("exit_code", 1)) != 0:
        return None

    try:
        payload = DockerfileUserCheckResult.model_validate_json(str(record.get("stdout", "")))
    except (ValidationError, json.JSONDecodeError, TypeError, ValueError):
        return Evidence(
            id=f"evidence-{uuid4().hex[:10]}",
            action_id=action.id,
            type="dockerfile_user_check",
            summary="Executor completed, but Dockerfile USER evidence was malformed.",
            artifact_refs=artifact_refs,
            reliability="low",
            verdict="inconclusive",
            details={"schema_valid": False},
        )

    return Evidence(
        id=f"evidence-{uuid4().hex[:10]}",
        action_id=action.id,
        type="dockerfile_user_check",
        summary=payload.explanation,
        artifact_refs=artifact_refs,
        reliability="high",
        verdict=payload.verdict,
        details=payload.model_dump(by_alias=True),
    )
