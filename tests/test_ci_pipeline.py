from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from app.ci_pipeline import (
    _validate_allowed_repository,
    run_ci_pipeline,
    target_subprocess_environment,
)
from app.config import settings
from app.pipeline_run import run_pipeline
from schemas.runtime import RuntimeService, RuntimeServiceMap
from schemas.runtime_telemetry import RuntimeTelemetryTimeline
from schemas.target import TargetProfile


def _args(tmp_path: Path, target: Path, profile: Path) -> argparse.Namespace:
    return argparse.Namespace(
        target=str(target),
        target_repository=None,
        profile=str(profile),
        architecture=None,
        architecture_overrides=None,
        sast_config="auto",
        findings=None,
        output_dir=str(tmp_path / "artifacts"),
        agent_mode="stub",
        max_iterations=1,
        full_reports=False,
        base_ref=None,
        head_ref="HEAD",
        base_findings=None,
        base_architecture=None,
    )


def _profile(tmp_path: Path, target: Path) -> Path:
    architecture = tmp_path / "architecture.yaml"
    architecture.write_text(
        "services:\n  backend:\n    type: api\n    public: true\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        yaml.safe_dump(
            {
                "id": "demo",
                "repository_path": str(target),
                "architecture": {"file": str(architecture)},
                "runtime": {
                    "type": "docker_compose",
                    "base_url": "http://127.0.0.1:8000",
                    "compose_file": "docker-compose.yml",
                },
                "services": {
                    "backend": {
                        "type": "django",
                        "root": ".",
                        "compose_service": "backend",
                        "internal_port": 8000,
                        "healthcheck": {"path": "/health/"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return profile


def test_target_environment_removes_ci_and_llm_secrets() -> None:
    clean = target_subprocess_environment(
        {
            "PATH": "/usr/bin",
            "NORMAL_SETTING": "ok",
            "GROQ_API_KEY": "secret",
            "GITHUB_TOKEN": "secret",
            "DATABASE_PASSWORD": "secret",
            "CFT_LLM_ROUTES": "secret-route",
        }
    )

    assert clean == {"PATH": "/usr/bin", "NORMAL_SETTING": "ok"}


def test_repository_is_bound_to_trusted_profile_metadata(tmp_path: Path) -> None:
    profile = TargetProfile.model_validate(
        {
            "id": "demo",
            "repository_path": tmp_path,
            "metadata": {"ci.repository": "Team/target"},
        }
    )

    _validate_allowed_repository(profile, "team/TARGET")

    try:
        _validate_allowed_repository(profile, "other/target")
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("untrusted repository must be rejected")


def test_complete_ci_order_uses_one_managed_session(tmp_path: Path) -> None:
    events: list[str] = []
    target = tmp_path / "target"
    target.mkdir()
    (target / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    profile_path = _profile(tmp_path, target)

    class Discovery:
        def discover(self, repository: Path):
            events.append("discovery")

            class Result:
                def model_dump(self, **_: object) -> dict[str, object]:
                    return {"repository_root": str(repository), "components": []}

            return Result()

        def build_profile(self, result, *, base_profile):
            events.append("profile")
            return base_profile

    class Session:
        session_id = "session-1"
        adapter = "docker_compose"

        def __enter__(self):
            events.append("start")
            return self

        def __exit__(self, *_: object) -> bool:
            events.append("teardown")
            return False

        def collect_state(self) -> dict[str, object]:
            return {"status": "ready", "session_id": self.session_id}

        def collect_telemetry(self, *, run_id: str | None = None):
            events.append("telemetry")
            return RuntimeTelemetryTimeline(
                session_id=self.session_id,
                run_id=run_id,
                target="demo",
                events=[],
            )

    class Manager:
        def open(self, profile):
            events.append("manager")
            return Session()

    class Builder:
        def build(self, profile, session):
            events.append("runtime_map")
            return RuntimeServiceMap(
                session_id=session.session_id,
                network_name="demo_default",
                services={
                    "backend": RuntimeService(
                        name="backend",
                        address="http://backend:8000",
                        ready=True,
                        readiness_source="compose_health",
                        allowed_endpoints=["/health/"],
                    )
                },
            )

    def pipeline(args, *, profile_override, runtime_services):
        events.append("pipeline")
        assert profile_override.id == "demo"
        assert runtime_services.session_id == "session-1"
        return 0

    result = run_ci_pipeline(
        _args(tmp_path, target, profile_path),
        discovery_service=Discovery(),
        sandbox_manager=Manager(),
        runtime_builder=Builder(),
        pipeline_runner=pipeline,
    )

    assert result == 0
    assert events == [
        "discovery",
        "profile",
        "manager",
        "start",
        "runtime_map",
        "pipeline",
        "telemetry",
        "teardown",
    ]
    assert (tmp_path / "artifacts" / "runtime-service-map.json").is_file()
    assert (tmp_path / "artifacts" / "telemetry-index.json").is_file()


def test_stage_failure_writes_technical_gate(tmp_path: Path) -> None:
    missing = tmp_path / "missing-target"
    args = _args(tmp_path, missing, tmp_path / "missing-profile.yaml")

    assert run_ci_pipeline(args) == 2
    gate = json.loads((tmp_path / "artifacts" / "gate.json").read_text(encoding="utf-8"))

    assert gate["exit_code"] == 2
    assert gate["decision_basis"] == "technical_pipeline_error"


def test_workflow_runs_full_target_and_always_uploads_artifacts() -> None:
    workflow = Path(".github/workflows/security-pipeline.yml").read_text(encoding="utf-8")

    assert "path: target" in workflow
    assert "fetch-depth: 0" in workflow
    assert "persist-credentials: false" in workflow
    assert "python -m app.ci_pipeline" in workflow
    assert "if: always()" in workflow
    assert "--target ../target" in workflow


def test_workflow_runs_ruff_and_boundary_tests_for_both_registered_targets() -> None:
    workflow = Path(".github/workflows/security-pipeline.yml").read_text(encoding="utf-8")

    assert "python -m ruff check ." in workflow
    assert "python -m pytest -q -ra" in workflow
    assert "Ultramind-AI/sberlab_hack" in workflow
    assert "stavrmoris/autodealer" in workflow
    assert "SBERLAB_TARGET_PATH: ../sberlab-target" in workflow
    assert "AUTODEALER_TARGET_PATH: ../autodealer-target" in workflow
    assert "tests/test_docker_sandbox_boundary.py" in workflow
    assert "tests/test_sandbox_manager_integration.py" in workflow


def test_repository_level_finding_does_not_become_technical_failure(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    workflow_dir = target / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = workflow_dir / "security.yml"
    workflow.write_text("steps:\n  - uses: actions/checkout@v4\n", encoding="utf-8")
    profile_path = _profile(tmp_path, target)
    findings = tmp_path / "findings.json"
    findings.write_text(
        json.dumps(
            [
                {
                    "id": "mutable-action:.github/workflows/security.yml:2",
                    "source": "semgrep",
                    "rule_id": "github-actions-mutable-action-tag",
                    "title": "GitHub Action is not pinned by digest",
                    "description": "Use an immutable commit digest.",
                    "file": ".github/workflows/security.yml",
                    "line_start": 2,
                    "line_end": 2,
                    "severity": "WARNING",
                    "service": None,
                }
            ]
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        profile=str(profile_path),
        target=str(target),
        architecture=None,
        architecture_overrides=None,
        sast_config="auto",
        output_dir=str(tmp_path / "pipeline-artifacts"),
        findings=str(findings),
        agent_mode="stub",
        max_iterations=1,
        full_reports=False,
        base_ref=None,
        head_ref="HEAD",
        base_findings=None,
        base_architecture=None,
    )

    settings_snapshot = (
        settings.target_file,
        settings.target_repository_path,
        settings.agent_mode,
    )
    try:
        assert run_pipeline(args) == 0

        gate = json.loads(
            (tmp_path / "pipeline-artifacts" / "gate.json").read_text(encoding="utf-8")
        )
        reports = json.loads(
            (tmp_path / "pipeline-artifacts" / "reports-index.json").read_text(
                encoding="utf-8"
            )
        )
        assert gate["decision"] == "warn"
        assert gate["technical_errors"] == 0
        assert gate["inconclusive"] == 1
        assert reports[0]["status"] == "inconclusive"
    finally:
        (
            settings.target_file,
            settings.target_repository_path,
            settings.agent_mode,
        ) = settings_snapshot
