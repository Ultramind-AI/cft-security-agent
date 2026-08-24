from __future__ import annotations

import argparse
import base64
import io
import json
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.registry import ApiTargetRegistry
from api.service import RunOrchestrator
from api.store import ApiStore
from pipeline.progress import PipelineProgressRecorder
from schemas.agent_loop import AgentDecisionRecord
from schemas.api import CreateChatSessionRequest, CreateRunRequest
from schemas.errors import ErrorDetail
from schemas.evidence import (
    Evidence,
    EvidenceAction,
    EvidenceObservation,
    EvidenceScope,
)
from schemas.pipeline import GateResult, PipelineFindingResult
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
        findings=[
            PipelineFindingResult(
                finding_id="finding-1",
                status="confirmed",
                gate_effect="fail",
                category="confirmed_risk",
                reason="Confirmed HIGH finding blocks the gate.",
                report_path=str((reports_dir / "000-finding-1.json").resolve()),
            )
        ],
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
        assert all(
            finding["report_path"] is None
            for finding in gate.json()["findings"]
        )


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


def test_chat_describes_technical_failure_without_security_verdict(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path, pipeline_runner=_technical_failure_pipeline)
    with TestClient(create_app(orchestrator)) as client:
        session = client.post(
            "/chat/sessions", json={"target_id": "demo-target"}
        ).json()
        started = client.post(
            f"/chat/sessions/{session['id']}/messages",
            json={"content": "Проведи анализ", "agent_mode": "stub"},
        ).json()
        run_id = started["session"]["active_run_id"]
        _wait_for_completion(client, run_id)

        snapshot = client.get(f"/chat/sessions/{session['id']}").json()
        summary = next(
            message["content"]
            for message in snapshot["messages"]
            if message["kind"] == "summary"
        )

    assert "технической ошибки" in summary
    assert "Результат анализа безопасности не сформирован" in summary
    assert "Анализ завершён" not in summary



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
                "agent_mode": "llm",
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
        assert [item["run"]["id"] for item in snapshot["runs"]] == [run_id]
        assert snapshot["reports"][0]["finding_id"] == "finding-1"
        assert snapshot["gate"]["decision"] == "fail"
        summary = next(
            message for message in snapshot["messages"] if message["kind"] == "summary"
        )
        assert "Ответ по Evidence" in summary["content"]

        followup = client.post(
            f"/chat/sessions/{session_id}/messages",
            json={"content": "Что именно подтверждено?", "agent_mode": "stub"},
        )
        assert followup.status_code == 200
        messages = followup.json()["messages"]
        assert messages[-1]["role"] == "assistant"
        assert "Ответ по Evidence" in messages[-1]["content"]
        assert followup.json()["session"]["active_run_id"] == run_id


def test_chat_snapshot_retains_multiple_analysis_runs(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        session = client.post(
            "/chat/sessions", json={"target_id": "demo-target"}
        ).json()
        session_id = session["id"]

        first = client.post(
            f"/chat/sessions/{session_id}/messages",
            json={"content": "Проведи полный анализ", "agent_mode": "stub"},
        ).json()
        first_run_id = first["session"]["active_run_id"]
        _wait_for_completion(client, first_run_id)

        second = client.post(
            f"/chat/sessions/{session_id}/messages",
            json={"content": "Перепроверь auth глубже", "agent_mode": "stub"},
        ).json()
        second_run_id = second["session"]["active_run_id"]
        assert second_run_id != first_run_id
        _wait_for_completion(client, second_run_id)

        snapshot = client.get(f"/chat/sessions/{session_id}").json()
        assert [item["run"]["id"] for item in snapshot["runs"]] == [
            first_run_id,
            second_run_id,
        ]
        assert all(item["reports"] for item in snapshot["runs"])
        assert all(item["gate"]["decision"] == "fail" for item in snapshot["runs"])


def test_delete_chat_removes_conversation_but_preserves_completed_run(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        session = client.post(
            "/chat/sessions", json={"target_id": "demo-target"}
        ).json()
        started = client.post(
            f"/chat/sessions/{session['id']}/messages",
            json={"content": "Проведи полный анализ", "agent_mode": "stub"},
        ).json()
        run_id = started["session"]["active_run_id"]
        _wait_for_completion(client, run_id)

        response = client.delete(f"/chat/sessions/{session['id']}")

        assert response.status_code == 204
        assert client.get(f"/chat/sessions/{session['id']}").status_code == 404
        assert client.get(f"/runs/{run_id}").status_code == 200
        assert all(item["id"] != session["id"] for item in client.get("/chat/sessions").json())


def test_delete_chat_rejects_active_analysis(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    session = orchestrator.create_chat_session(
        CreateChatSessionRequest(target_id="demo-target")
    )
    run_id = "run-active-delete-test"
    orchestrator.store.create_run(
        run_id=run_id,
        target_id="demo-target",
        agent_mode="stub",
        max_iterations=1,
        analysis_request="test",
        artifact_dir=tmp_path / "artifacts" / run_id,
    )
    orchestrator.store.set_chat_run(session.id, run_id)

    with TestClient(create_app(orchestrator)) as client:
        response = client.delete(f"/chat/sessions/{session.id}")

        assert response.status_code == 409
        assert "analysis is active" in response.json()["detail"]
        assert client.get(f"/chat/sessions/{session.id}").status_code == 200



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


def _folder_manifest() -> dict:
    files = {
        "manage.py": "print('manage')\n",
        "requirements.txt": "Django==5.0\n",
        "backend/requirements.txt": "Django==5.0\n",
        "backend/app/__init__.py": "",
        "backend/app/views.py": "password = ''\n",
        "Dockerfile": "FROM python:3.11-slim\n",
        "docker-compose.yml": (
            "services:\n  app:\n    build: .\n    ports:\n      - '8000:8000'\n"
        ),
    }
    return {
        "name": "folder demo",
        "files": [
            {"path": path, "content_base64": base64.b64encode(content.encode()).decode()}
            for path, content in files.items()
        ],
    }


def test_api_imports_folder_manifest_with_nested_paths(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        response = client.post("/projects/import-files", json=_folder_manifest())
        assert response.status_code == 201
        project = response.json()

        assert project["id"].startswith("upload-")
        assert project["name"] == "folder demo"
        assert project["repository_available"] is True
        assert project["services"]

        registered = orchestrator.registry.get(project["id"])
        repository = registered.profile.repository_path
        assert repository is not None
        # Directory structure must survive the import.
        assert (repository / "backend" / "app" / "views.py").is_file()
        assert (repository / "backend" / "app" / "__init__.py").read_bytes() == b""


def test_api_rejects_unsafe_folder_manifest_paths(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        for unsafe in ("../escape.txt", "/etc/passwd", "C:evil.py", "a/../../b.py"):
            payload = {
                "name": "bad",
                "files": [
                    {"path": unsafe, "content_base64": base64.b64encode(b"x").decode()}
                ],
            }
            response = client.post("/projects/import-files", json=payload)
            assert response.status_code == 422, unsafe
            assert "escapes" in response.json()["detail"] or "path" in response.json()["detail"]


def test_api_rejects_invalid_base64_folder_manifest(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    with TestClient(create_app(orchestrator)) as client:
        payload = {
            "name": "bad",
            "files": [{"path": "manage.py", "content_base64": "!!!not-base64!!!"}],
        }
        response = client.post("/projects/import-files", json=payload)

    assert response.status_code == 422


def test_api_rejects_oversized_folder_manifest(tmp_path: Path) -> None:
    orchestrator = RunOrchestrator(
        registry=_target_registry(tmp_path),
        store=ApiStore(tmp_path / "api.sqlite3"),
        artifact_root=tmp_path / "artifacts",
        project_root=tmp_path / "uploaded-projects",
        pipeline_runner=_fake_pipeline,
        max_upload_bytes=64,
    )
    with TestClient(create_app(orchestrator)) as client:
        payload = {
            "name": "big",
            "files": [
                {"path": "manage.py", "content_base64": base64.b64encode(b"x" * 4096).decode()}
            ],
        }
        response = client.post("/projects/import-files", json=payload)

    assert response.status_code == 422


def _progress_pipeline(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    recorder = PipelineProgressRecorder(output_dir)
    recorder.discovery_done("1 components: python, django")
    recorder.sast_done(2)
    recorder.finding_started(
        index=1,
        total=2,
        finding_id="finding-1",
        title="Demo finding",
        severity="HIGH",
        rule_id="demo.rule",
        file="app.py",
    )
    recorder.finding_finished(finding_id="finding-1", status="confirmed")

    audit_dir = output_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_record = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "run_id": args.output_dir,
        "session_id": "session-1",
        "action_id": "action-1",
        "tool": "observe_http_response",
        "target": "demo-target",
        "status": "completed",
        "exit_code": 0,
        "duration_ms": 120,
        "evidence_ref": "evidence:evidence-1",
        "proposal_digest": "a" * 64,
        "evidence_digest": "b" * 64,
        "policy_digest": "c" * 64,
        "runtime_backend": "docker",
        "network_mode": "none",
    }
    (audit_dir / "executor.jsonl").write_text(
        json.dumps(audit_record) + "\n", encoding="utf-8"
    )

    discovery = {
        "repository_root": str(output_dir),
        "components": [
            {
                "id": "app",
                "root": ".",
                "technologies": ["python"],
                "frameworks": ["django"],
                "dependency_files": ["requirements.txt"],
                "dockerfiles": ["Dockerfile"],
                "compose_candidates": [],
                "build_candidates": [],
                "run_candidates": [],
                "healthcheck_candidates": [],
                "allowed_local_addresses": ["127.0.0.1:8000"],
                "source_paths": ["."],
                "confidence": 0.9,
            }
        ],
        "signals": [],
        "project_files": [],
        "warnings": [],
    }
    (output_dir / "discovery.json").write_text(
        json.dumps(discovery), encoding="utf-8"
    )
    (output_dir / "target-profile.json").write_text(
        json.dumps({"services": {"app": {"type": "django"}}}), encoding="utf-8"
    )
    return _fake_pipeline(args)


def test_api_serves_progress_and_discovery_during_and_after_run(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path, pipeline_runner=_progress_pipeline)
    with TestClient(create_app(orchestrator)) as client:
        response = client.post("/runs", json={"target_id": "demo-target"})
        run_id = response.json()["id"]

        progress_payload: dict | None = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            progress = client.get(f"/runs/{run_id}/progress")
            assert progress.status_code == 200
            progress_payload = progress.json()
            # Stages and the executor audit trail are written at slightly
            # different moments by the background pipeline thread.
            if progress_payload["stages"] and progress_payload["activities"]:
                break
            time.sleep(0.05)
        assert progress_payload is not None and progress_payload["stages"]
        assert progress_payload["activities"], "executor audit activity missing"

        stage_names = [stage["stage"] for stage in progress_payload["stages"]]
        assert "discovery" in stage_names
        sast_stage = next(item for item in progress_payload["stages"] if item["stage"] == "sast")
        assert sast_stage["status"] == "done"
        assert "2 findings" in (sast_stage["detail"] or "")
        assert progress_payload["findings_total"] == 2
        assert progress_payload["findings_done"] >= 1
        assert progress_payload["finding_events"][0]["status"] == "started"
        assert progress_payload["finding_events"][0]["title"] == "Demo finding"
        assert progress_payload["finding_events"][1]["status"] == "finished"
        assert progress_payload["finding_events"][1]["result"] == "confirmed"
        activities = progress_payload["activities"]
        assert activities[0]["tool"] == "observe_http_response"
        assert activities[0]["exit_code"] == 0
        assert "digest" not in json.dumps(activities)

        discovery = client.get(f"/runs/{run_id}/discovery")
        assert discovery.status_code == 200
        discovery_text = discovery.text
        assert "django" in discovery_text
        # The sanitized view must never leak local artifact or repository paths.
        assert str(orchestrator.artifact_root) not in discovery_text

        completed = _wait_for_completion(client, run_id)
        assert completed["status"] == "completed"

        final_progress = client.get(f"/runs/{run_id}/progress").json()
        assert final_progress["findings_total"] == 2


def test_chat_session_rejects_project_without_repository(tmp_path: Path) -> None:
    # Build the trusted profile, then remove the checkout it points to so the
    # project is registered but its repository_available becomes False.
    registry = _target_registry(tmp_path)
    import shutil as _shutil

    _shutil.rmtree(tmp_path / "target-repo")
    store = ApiStore(tmp_path / "api.sqlite3")
    orchestrator = RunOrchestrator(
        registry=registry,
        store=store,
        artifact_root=tmp_path / "artifacts",
        project_root=tmp_path / "uploaded-projects",
        pipeline_runner=_fake_pipeline,
    )
    with TestClient(create_app(orchestrator)) as client:
        assert client.get("/projects").json()[0]["repository_available"] is False
        response = client.post(
            "/chat/sessions", json={"target_id": "demo-target"}
        )
        assert response.status_code == 422
        assert "not available" in response.json()["detail"]


def test_chat_snapshot_includes_progress_and_discovery(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path, pipeline_runner=_progress_pipeline)
    with TestClient(create_app(orchestrator)) as client:
        session = client.post(
            "/chat/sessions", json={"target_id": "demo-target"}
        ).json()
        started = client.post(
            f"/chat/sessions/{session['id']}/messages",
            json={"content": "Проведи полный анализ", "agent_mode": "stub"},
        )
        assert started.status_code == 200
        snapshot = started.json()
        run_id = snapshot["session"]["active_run_id"]
        _wait_for_completion(client, run_id)

        snapshot = client.get(f"/chat/sessions/{session['id']}").json()
        assert snapshot["progress"]["findings_total"] == 2
        assert snapshot["discovery"]["technologies"]

        completed_snapshot = client.get(f"/chat/sessions/{session['id']}").json()
        assert completed_snapshot["gate"]["decision"] == "fail"
        assert completed_snapshot["progress"]["stages"]
