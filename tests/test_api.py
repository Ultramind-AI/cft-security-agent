from __future__ import annotations

import argparse
import io
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.registry import ApiTargetRegistry
from api.service import RunOrchestrator
from api.store import ApiStore
from schemas.agent_loop import AgentDecisionRecord
from schemas.api import CreateChatSessionRequest, CreateRunRequest
from schemas.errors import ErrorDetail
from schemas.evidence import (
    Evidence,
    EvidenceAction,
    EvidenceObservation,
    EvidenceScope,
)
from schemas.pipeline import GateResult
from schemas.report import (
    FinalReport,
    ReportFinding,
    SandboxActionSummary,
    VerificationSummary,
)


def _target_registry(tmp_path: Path) -> ApiTargetRegistry:
    target_root = tmp_path / "target-repo"
    target_root.mkdir()
    architecture = tmp_path / "architecture.yaml"
    architecture.write_text("services: {}\n", encoding="utf-8")

    trusted_root = tmp_path / "targets"
    trusted_root.mkdir()
    profile = trusted_root / "demo.yaml"
    profile.write_text(
        "\n".join(
            [
                "id: demo-target",
                "name: Demo Target",
                f"repository_path: {target_root}",
                "environment: local",
                "architecture:",
                f"  file: {architecture}",
                "services:",
                "  app:",
                "    type: django",
                "    root: .",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return ApiTargetRegistry.from_profile_paths(
        [profile],
        trusted_root=trusted_root,
    )


def _evidence() -> Evidence:
    return Evidence(
        id="evidence-1",
        action_id="action-1",
        type="http_status_observation",
        summary="Registered sandbox service returned HTTP 200.",
        reliability="high",
        verdict="confirmed",
        source="runtime",
        sandbox_session_id="session-1",
        hypothesis_id="hypothesis-1",
        action=EvidenceAction(
            id="action-1",
            tool="observe_http_response",
            run_id="run-1",
        ),
        observation=EvidenceObservation(
            kind="http_status_observation",
            facts={"status_code": 200},
        ),
        scope=EvidenceScope(
            target="demo-target",
            environment="local",
            service="app",
            description="registered sandbox service",
        ),
    )


def _report() -> FinalReport:
    evidence = _evidence()
    return FinalReport(
        finding_id="finding-1",
        finding=ReportFinding(
            id="finding-1",
            source="semgrep",
            rule_id="demo.rule",
            title="Demo finding",
            severity="HIGH",
            service="app",
            file="app.py",
            line_start=7,
        ),
        status="confirmed",
        verification=VerificationSummary(
            action_id="action-1",
            capability="observe_http_response",
            target="demo-target",
            environment="local",
            validator_decision="approved",
            evidence_count=1,
            evidence_types=[evidence.type],
            decision_basis="capability_specific_evidence",
        ),
        evidence=[evidence],
        sandbox_actions=[
            SandboxActionSummary(
                action_id="action-1",
                capability="observe_http_response",
                target="demo-target",
                environment="local",
                purpose="Observe the registered local service.",
                execution_status="completed",
                exit_code=0,
            )
        ],
        agent_decisions=[
            AgentDecisionRecord(
                step=1,
                outcome="stop",
                reason="Terminal Evidence is available.",
                evidence_ids=[evidence.id],
                stop_reason="terminal_evidence",
            )
        ],
        explanation="The deterministic Evidence confirms the reported condition.",
        next_step="Review and remediate the finding.",
        iterations=1,
        stop_reason="terminal_evidence",
    )


def _fake_pipeline(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = _report()
    (reports_dir / "000-finding-1.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    gate = GateResult(
        decision="fail",
        exit_code=1,
        decision_basis="confirmed_risk",
        reports_total=1,
        confirmed=1,
        reasons=["Confirmed HIGH finding blocks the gate."],
    )
    (output_dir / "gate.json").write_text(
        gate.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return gate.exit_code


def _technical_failure_pipeline(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    error = ErrorDetail(
        code="DEPENDENCY_UNAVAILABLE",
        layer="pipeline",
        message="Managed pipeline dependency is unavailable",
        retryable=True,
    )
    gate = GateResult(
        decision="fail",
        exit_code=2,
        decision_basis="technical_pipeline_error",
        technical_errors=1,
        errors=[error],
    )
    (output_dir / "gate.json").write_text(
        gate.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return 2


def _orchestrator(
    tmp_path: Path,
    pipeline_runner=_fake_pipeline,
    *,
    chat_answerer=None,
) -> RunOrchestrator:
    return RunOrchestrator(
        registry=_target_registry(tmp_path),
        store=ApiStore(tmp_path / "api.sqlite3"),
        artifact_root=tmp_path / "artifacts",
        project_root=tmp_path / "uploaded-projects",
        pipeline_runner=pipeline_runner,
        chat_answerer=chat_answerer,
    )


def _wait_for_completion(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        payload = client.get(f"/runs/{run_id}").json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("API run did not finish in time")


def test_api_runs_the_canonical_pipeline_and_serves_persisted_results(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        projects = client.get("/projects")
        assert projects.status_code == 200
        assert projects.json() == [
            {
                "id": "demo-target",
                "name": "Demo Target",
                "environment": "local",
                "services": ["app"],
                "repository_available": True,
            }
        ]

        response = client.post(
            "/runs",
            json={"target_id": "demo-target", "max_iterations": 3},
        )
        assert response.status_code == 202
        run_id = response.json()["id"]
        completed = _wait_for_completion(client, run_id)

        assert completed["status"] == "completed"
        assert completed["exit_code"] == 1
        assert completed["gate_decision"] == "fail"
        assert "artifact_dir" not in completed

        findings = client.get(f"/runs/{run_id}/findings").json()
        assert findings[0]["finding_id"] == "finding-1"
        assert findings[0]["status"] == "confirmed"

        evidence = client.get(f"/runs/{run_id}/evidence").json()
        assert evidence[0]["evidence"]["id"] == "evidence-1"
        assert evidence[0]["evidence"]["observation"]["facts"]["status_code"] == 200

        timeline = client.get(f"/runs/{run_id}/timeline").json()
        assert timeline["findings"][0]["agent_decisions"][0]["outcome"] == "stop"
        assert timeline["findings"][0]["sandbox_actions"][0]["action_id"] == "action-1"

        report = client.get(f"/runs/{run_id}/reports/finding-1")
        assert report.status_code == 200
        assert report.json()["finding_id"] == "finding-1"

        gate = client.get(f"/runs/{run_id}/gate")
        assert gate.status_code == 200
        assert gate.json()["decision"] == "fail"


def test_api_streams_run_status_until_completion(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        response = client.post("/runs", json={"target_id": "demo-target"})
        run_id = response.json()["id"]
        events = client.get(f"/runs/{run_id}/events")

    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: run" in events.text
    assert '"status":"completed"' in events.text
    assert "event: done" in events.text


def test_api_rejects_unregistered_target(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        response = client.post(
            "/runs",
            json=CreateRunRequest(target_id="not-registered").model_dump(mode="json"),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown registered target"


def test_registry_rejects_profile_outside_trusted_root(tmp_path: Path) -> None:
    trusted_root = tmp_path / "targets"
    trusted_root.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("id: outside\nenvironment: local\n", encoding="utf-8")

    try:
        ApiTargetRegistry.from_profile_paths([outside], trusted_root=trusted_root)
    except ValueError as exc:
        assert "trusted target root" in str(exc)
    else:
        raise AssertionError("Profile outside trusted root must be rejected")


def test_api_keeps_technical_failure_separate_from_security_gate_failure(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path, pipeline_runner=_technical_failure_pipeline)
    with TestClient(create_app(orchestrator)) as client:
        response = client.post("/runs", json={"target_id": "demo-target"})
        run_id = response.json()["id"]
        completed = _wait_for_completion(client, run_id)

    assert completed["status"] == "technical_failure"
    assert completed["exit_code"] == 2
    assert completed["gate_decision"] == "fail"
    assert completed["error"]["code"] == "DEPENDENCY_UNAVAILABLE"



def _uploaded_project_zip(*, traversal: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if traversal:
            archive.writestr("../escape.txt", "nope")
        else:
            archive.writestr("demo/manage.py", "print('manage')\n")
            archive.writestr("demo/requirements.txt", "Django==5.0\n")
            archive.writestr("demo/Dockerfile", "FROM python:3.11-slim\n")
            archive.writestr(
                "demo/docker-compose.yml",
                "services:\n  app:\n    build: .\n    ports:\n      - '8000:8000'\n",
            )
    return buffer.getvalue()


def test_api_imports_zip_with_discovery_and_registers_project(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        response = client.post(
            "/projects/import",
            headers={"X-Project-Filename": "demo.zip", "Content-Type": "application/zip"},
            content=_uploaded_project_zip(),
        )
        assert response.status_code == 201
        project = response.json()
        assert project["id"].startswith("upload-")
        assert project["name"] == "demo"
        assert project["repository_available"] is True
        assert project["services"]

        registered = orchestrator.registry.get(project["id"])
        assert registered.profile.repository_path is not None
        assert registered.profile.repository_path.is_dir()
        assert registered.profile.architecture.file is not None
        assert registered.profile.architecture.file.is_file()


def test_api_rejects_zip_path_traversal(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        response = client.post(
            "/projects/import",
            headers={"X-Project-Filename": "bad.zip", "Content-Type": "application/zip"},
            content=_uploaded_project_zip(traversal=True),
        )

    assert response.status_code == 422
    assert "escapes" in response.json()["detail"]


def test_chat_starts_analysis_streams_persisted_result_and_answers_followup(
    tmp_path: Path,
) -> None:
    captured_requests: list[str | None] = []

    def pipeline(args: argparse.Namespace) -> int:
        captured_requests.append(args.analysis_request)
        return _fake_pipeline(args)

    orchestrator = _orchestrator(
        tmp_path,
        pipeline_runner=pipeline,
        chat_answerer=lambda question, context: (
            f"Ответ по Evidence: {question}; gate={context['gate']['decision']}"
        ),
    )
    with TestClient(create_app(orchestrator)) as client:
        session_response = client.post(
            "/chat/sessions",
            json={"target_id": "demo-target", "title": "Demo chat"},
        )
        assert session_response.status_code == 201
        session_id = session_response.json()["id"]

        first = client.post(
            f"/chat/sessions/{session_id}/messages",
            json={
                "content": "Проведи полный анализ и обрати внимание на auth",
                "agent_mode": "stub",
                "max_iterations": 3,
            },
        )
        assert first.status_code == 200
        run_id = first.json()["session"]["active_run_id"]
        completed = _wait_for_completion(client, run_id)
        assert completed["analysis_request"] == "Проведи полный анализ и обрати внимание на auth"
        assert captured_requests == ["Проведи полный анализ и обрати внимание на auth"]

        snapshot = client.get(f"/chat/sessions/{session_id}").json()
        assert snapshot["run"]["status"] == "completed"
        assert snapshot["reports"][0]["finding_id"] == "finding-1"
        assert snapshot["gate"]["decision"] == "fail"
        assert any(message["kind"] == "summary" for message in snapshot["messages"])

        followup = client.post(
            f"/chat/sessions/{session_id}/messages",
            json={"content": "Что именно подтверждено?", "agent_mode": "stub"},
        )
        assert followup.status_code == 200
        messages = followup.json()["messages"]
        assert messages[-1]["role"] == "assistant"
        assert "Ответ по Evidence" in messages[-1]["content"]
        assert followup.json()["session"]["active_run_id"] == run_id



def test_generated_upload_profile_is_reloaded_after_service_restart(tmp_path: Path) -> None:
    store = ApiStore(tmp_path / "api.sqlite3")
    project_root = tmp_path / "uploaded-projects"
    first = RunOrchestrator(
        registry=_target_registry(tmp_path),
        store=store,
        artifact_root=tmp_path / "artifacts",
        project_root=project_root,
        pipeline_runner=_fake_pipeline,
    )
    project = first.import_project_zip(filename="restart-demo.zip", content=_uploaded_project_zip())
    first.close()

    second_registry = ApiTargetRegistry.from_profile_paths(
        [tmp_path / "targets" / "demo.yaml"],
        trusted_root=tmp_path / "targets",
    )
    second = RunOrchestrator(
        registry=second_registry,
        store=store,
        artifact_root=tmp_path / "artifacts-2",
        project_root=project_root,
        pipeline_runner=_fake_pipeline,
    )
    try:
        registered = second.registry.get(project.id)
        assert registered.profile.repository_path is not None
        assert registered.profile.repository_path.is_dir()
        session = second.create_chat_session(
            CreateChatSessionRequest(target_id=project.id)
        )
        assert session.target_id == project.id
    finally:
        second.close()
