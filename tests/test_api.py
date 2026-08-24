from __future__ import annotations

import argparse
import time
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.registry import ApiTargetRegistry
from api.service import RunOrchestrator
from api.store import ApiStore
from schemas.agent_loop import AgentDecisionRecord
from schemas.api import CreateRunRequest
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


def _orchestrator(tmp_path: Path, pipeline_runner=_fake_pipeline) -> RunOrchestrator:
    return RunOrchestrator(
        registry=_target_registry(tmp_path),
        store=ApiStore(tmp_path / "api.sqlite3"),
        artifact_root=tmp_path / "artifacts",
        pipeline_runner=pipeline_runner,
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
