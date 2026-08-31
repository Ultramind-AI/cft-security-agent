"""Преобразование HTTP-результата в проверяемое док-во."""

from __future__ import annotations

import json
from uuid import uuid4

from pydantic import ValidationError

from schemas.action import ActionProposal
from schemas.evidence import (
    Evidence,
    EvidenceAction,
    EvidenceArtifact,
    EvidenceObservation,
    EvidenceScope,
)
from schemas.execution import ExecutionResult
from schemas.runtime_observations import HttpSurfaceObservationResult


def build_http_surface_evidence(
    *,
    action: ActionProposal,
    execution: ExecutionResult,
    record: dict[str, object],
    artifact_refs: list[str],
    hypothesis_id: str,
) -> list[Evidence]:
    """Создать семь фактических записей из сохраненного GET-наблюдения"""
    session_id = record.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return []
    try:
        payload = HttpSurfaceObservationResult.model_validate_json(
            str(record.get("stdout", ""))
        )
    except (ValidationError, TypeError, ValueError, json.JSONDecodeError):
        return []

    checks: tuple[tuple[str, str, dict[str, object]], ...] = (
        ("http_status", "HTTP response status", {"status_code": payload.status_code, "response_category": payload.response_category}),
        ("http_security_headers", "Selected HTTP security headers", {"headers": payload.security_headers}),
        ("http_cookie_attributes", "Cookie attribute observations without cookie values", {"cookies": [item.model_dump() for item in payload.cookies]}),
        ("http_cors", "CORS response headers", {"headers": payload.cors}),
        ("http_redirect", "Redirect behaviour without following redirects", {"location": payload.redirect_location, "target_is_local_path": payload.redirect_target_is_local_path}),
        ("http_health_or_error", "Health or error response classification", {"classification": payload.health_or_error_response}),
        ("http_route_access", "Known route accessibility", {"accessible": payload.route_accessible}),
    )
    artifacts = [_artifact(ref) for ref in artifact_refs]
    scope = EvidenceScope(
        target=action.target,
        environment=action.environment,
        service=action.service,
        description=f"GET {payload.endpoint} via registered RuntimeServiceMap service",
    )
    evidence_action = EvidenceAction(
        id=action.id,
        tool=action.tool,
        run_id=execution.run_id,
    )
    return [
        Evidence(
            id=f"evidence-{uuid4().hex[:10]}",
            action_id=action.id,
            type=kind,
            summary=summary,
            artifact_refs=artifact_refs,
            reliability="high",
            source="runtime",
            sandbox_session_id=session_id,
            hypothesis_id=hypothesis_id,
            action=evidence_action,
            observation=EvidenceObservation(kind=kind, facts=facts),
            scope=scope,
            artifacts=artifacts,
        )
        for kind, summary, facts in checks
    ]


def _artifact(reference: str) -> EvidenceArtifact:
    if reference.startswith("execution-"):
        role = "execution"
    elif reference.startswith("audit:"):
        role = "audit"
    else:
        role = "other"
    return EvidenceArtifact(ref=reference, role=role)
