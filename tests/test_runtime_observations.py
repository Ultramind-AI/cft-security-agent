from __future__ import annotations

import json
from email.message import Message

from evidence.runtime import build_http_surface_evidence
from executor import worker
from schemas.action import ActionProposal
from schemas.execution import ExecutionResult


class _Response:
    status = 302

    def __init__(self) -> None:
        self.headers = Message()
        self.headers["Location"] = "/login/"
        self.headers["Content-Security-Policy"] = "default-src 'self'"
        self.headers["Access-Control-Allow-Origin"] = "https://example.test"
        self.headers["Set-Cookie"] = "session=never-recorded; Secure; HttpOnly; SameSite=Lax"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _action() -> ActionProposal:
    return ActionProposal(
        id="observe-route",
        tool="observe_http_surface",
        target="target",
        environment="sandbox",
        service="api",
        endpoint="/health/",
        purpose="Collect bounded HTTP observations.",
        expected_evidence="Structured HTTP response observations.",
    )


def test_http_surface_worker_collects_bounded_headers_without_cookie_values(monkeypatch) -> None:
    monkeypatch.setattr("executor.worker.build_opener", lambda *_args: type("Opener", (), {"open": lambda *_args, **_kwargs: _Response()})())

    code, stdout, stderr = worker._execute(
        {
            "tool": "observe_http_surface",
            "base_url": "http://api:8000",
            "endpoint": "/health/",
            "parameters": {},
            "request_timeout_seconds": 1,
            "max_output_bytes": 1024,
        }
    )

    payload = json.loads(stdout)
    assert (code, stderr) == (0, "")
    assert payload["status_code"] == 302
    assert payload["redirect_target_is_local_path"] is True
    assert payload["cookies"] == [
        {"name": "session", "secure": True, "http_only": True, "same_site": "Lax"}
    ]
    assert "never-recorded" not in stdout


def test_one_trusted_http_result_becomes_seven_runtime_evidence_records() -> None:
    action = _action()
    execution = ExecutionResult(
        run_id="run-observe",
        action_id=action.id,
        status="completed",
        exit_code=0,
        evidence_ref="execution-observe",
        audit_ref="audit:run-observe",
    )
    record = {
        "session_id": "session-1",
        "stdout": json.dumps(
            {
                "schema": "cft.http_surface_observation.v1",
                "endpoint": "/health/",
                "status_code": 200,
                "response_category": "success",
                "route_accessible": True,
                "health_or_error_response": "health",
                "security_headers": {"x-frame-options": "DENY"},
                "cookies": [],
                "cors": {},
                "redirect_location": None,
                "redirect_target_is_local_path": None,
            }
        ),
    }

    evidence = build_http_surface_evidence(
        action=action,
        execution=execution,
        record=record,
        artifact_refs=["execution-observe", "audit:run-observe"],
        hypothesis_id="hypothesis-1",
    )

    assert len(evidence) == 7
    assert {item.type for item in evidence} == {
        "http_status", "http_security_headers", "http_cookie_attributes",
        "http_cors", "http_redirect", "http_health_or_error", "http_route_access",
    }
    assert all(item.source == "runtime" for item in evidence)
    assert all(item.sandbox_session_id == "session-1" for item in evidence)
    assert all(item.scope.service == "api" for item in evidence)
