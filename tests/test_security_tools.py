import json
from pathlib import Path

from agent.model import DeterministicAgentModel
from evidence.interpreter import build_evidence
from executor import worker
from schemas.action import ActionProposal
from schemas.architecture import ArchitectureContext
from schemas.evidence import (
    Evidence,
    EvidenceAction,
    EvidenceObservation,
    EvidenceScope,
)
from schemas.execution import ExecutionResult
from schemas.finding import Finding
from validator.validator import PolicyValidator


def _finding(
    *,
    rule_id: str,
    file: str,
    service: str,
    line: int = 1,
) -> Finding:
    return Finding(
        id=f"{rule_id}:{file}:{line}",
        source="semgrep",
        rule_id=rule_id,
        title="Security finding",
        description="Controlled verification required.",
        file=file,
        line_start=line,
        line_end=line,
        severity="WARNING",
        service=service,
    )


def _state(finding: Finding) -> dict:
    return {
        "finding": finding,
        "architecture_context": ArchitectureContext(
            service=finding.service or "unknown",
            public_exposure=True,
            criticality="high" if finding.service == "backend" else "medium",
        ),
        "iteration_count": 0,
        "max_iterations": 1,
        "evidence": [],
    }


def _artifacts() -> dict[str, dict[str, str]]:
    return {
        "backend_dockerfile": {"kind": "dockerfile", "path": "backend/Dockerfile"},
        "frontend_dockerfile": {
            "kind": "dockerfile",
            "path": "frontend/frontend/Dockerfile",
        },
        "demo_seed": {
            "kind": "python",
            "path": "backend/core/management/commands/seed_demo.py",
        },
        "user_model": {"kind": "python", "path": "backend/core/models.py"},
        "user_serializer": {
            "kind": "python",
            "path": "backend/core/serializers.py",
        },
        "user_views": {"kind": "python", "path": "backend/core/views.py"},
        "frontend_app": {
            "kind": "javascript",
            "path": "frontend/frontend/src/App.jsx",
        },
    }


def _write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _worker_payload(
    tmp_path: Path,
    *,
    tool: str,
    parameters: dict,
) -> dict:
    return {
        "tool": tool,
        "repository_path": str(tmp_path),
        "artifacts": _artifacts(),
        "parameters": parameters,
        "request_timeout_seconds": 1,
        "max_output_bytes": 16_384,
    }


def test_agent_maps_both_docker_findings_to_one_reusable_capability() -> None:
    model = DeterministicAgentModel()
    cases = [
        ("backend/Dockerfile", "backend", "backend_dockerfile"),
        ("frontend/frontend/Dockerfile", "frontend", "frontend_dockerfile"),
    ]

    for file, service, artifact_id in cases:
        state = _state(
            _finding(
                rule_id="dockerfile.security.missing-user.missing-user",
                file=file,
                service=service,
            )
        )
        analysis = model.analyse(state)
        hypothesis = model.form_hypothesis(state, analysis)
        proposal = model.propose_action(state, analysis, hypothesis)

        assert proposal.tool == "inspect_dockerfile_user"
        assert proposal.parameters == {"artifact_id": artifact_id}
        assert PolicyValidator.from_yaml(
            "policies/default.yaml",
            target_file="targets/sberlab.yaml",
        ).validate(proposal).approved is True


def test_agent_maps_password_finding_to_password_assignment_capability() -> None:
    model = DeterministicAgentModel()
    state = _state(
        _finding(
            rule_id="python.django.security.audit.unvalidated-password.unvalidated-password",
            file="backend/core/management/commands/seed_demo.py",
            service="backend",
        )
    )

    analysis = model.analyse(state)
    hypothesis = model.form_hypothesis(state, analysis)
    proposal = model.propose_action(state, analysis, hypothesis)

    assert proposal.tool == "inspect_python_password_assignment"
    assert proposal.parameters == {"artifact_id": "demo_seed"}


def test_agent_maps_react_finding_to_static_flow_capability() -> None:
    model = DeterministicAgentModel()
    state = _state(
        _finding(
            rule_id=(
                "typescript.react.security.audit.react-dangerouslysetinnerhtml."
                "react-dangerouslysetinnerhtml"
            ),
            file="frontend/frontend/src/App.jsx",
            service="frontend",
        )
    )

    analysis = model.analyse(state)
    hypothesis = model.form_hypothesis(state, analysis)
    proposal = model.propose_action(state, analysis, hypothesis)

    assert proposal.tool == "inspect_react_dangerous_html_flow"
    assert proposal.parameters["frontend_artifact_id"] == "frontend_app"
    assert proposal.parameters["field"] == "about"


def test_dockerfile_capability_confirms_missing_user_for_any_trusted_artifact(tmp_path) -> None:
    _write(tmp_path, "frontend/frontend/Dockerfile", "FROM nginx:alpine\nCMD [\"nginx\"]\n")

    exit_code, stdout, stderr = worker._execute(
        _worker_payload(
            tmp_path,
            tool="inspect_dockerfile_user",
            parameters={"artifact_id": "frontend_dockerfile"},
        )
    )

    payload = json.loads(stdout)
    assert exit_code == 0
    assert stderr == ""
    assert payload["verdict"] == "confirmed"
    assert payload["user_classification"] == "missing"
    assert payload["runtime_user_verified"] is False


def test_dockerfile_capability_rejects_explicit_non_root_user(tmp_path) -> None:
    _write(
        tmp_path,
        "backend/Dockerfile",
        "FROM python:3.11-slim\nWORKDIR /app\nUSER appuser\n",
    )

    exit_code, stdout, stderr = worker._execute(
        _worker_payload(
            tmp_path,
            tool="inspect_dockerfile_user",
            parameters={"artifact_id": "backend_dockerfile"},
        )
    )

    payload = json.loads(stdout)
    assert exit_code == 0
    assert stderr == ""
    assert payload["verdict"] == "rejected"
    assert payload["user_classification"] == "non_root"


def test_dockerfile_capability_never_accepts_agent_controlled_path(tmp_path) -> None:
    _write(tmp_path, "backend/Dockerfile", "FROM python:3.11-slim\n")

    exit_code, stdout, stderr = worker._execute(
        _worker_payload(
            tmp_path,
            tool="inspect_dockerfile_user",
            parameters={"artifact_id": "backend_dockerfile", "path": "../other"},
        )
    )

    assert exit_code == 1
    assert stdout == ""
    assert "Unsupported capability parameters" in stderr


def test_password_capability_confirms_unvalidated_assignment_and_redacts_values(tmp_path) -> None:
    _write(
        tmp_path,
        "backend/core/management/commands/seed_demo.py",
        (
            "DEMO_USERS = ({'username': 'demo-admin', 'password': "
            "'example-password', 'is_superuser': True},)\n\n"
            "def seed(user, password):\n"
            "    user.set_password(password)\n"
        ),
    )

    exit_code, stdout, stderr = worker._execute(
        _worker_payload(
            tmp_path,
            tool="inspect_python_password_assignment",
            parameters={"artifact_id": "demo_seed"},
        )
    )

    payload = json.loads(stdout)
    assert exit_code == 0
    assert stderr == ""
    assert payload["verdict"] == "confirmed"
    assert payload["set_password_calls"] == 1
    assert payload["validate_password_calls"] == 0
    assert payload["privileged_hardcoded_password_records"] == 1
    assert payload["password_values_redacted"] is True
    assert "example-password" not in stdout
    assert payload["runtime_auth_verified"] is False


def _write_react_flow_fixture(tmp_path: Path, *, sanitized: bool = False) -> None:
    expression = "DOMPurify.sanitize(user.about)" if sanitized else "user.about"
    _write(
        tmp_path,
        "frontend/frontend/src/App.jsx",
        (
            "const Profile = ({ user }) => "
            f"<div dangerouslySetInnerHTML={{{{ __html: {expression} }}}} />;\n"
        ),
    )
    _write(
        tmp_path,
        "backend/core/models.py",
        """class User(models.Model):\n    about = models.TextField(blank=True)\n""",
    )
    _write(
        tmp_path,
        "backend/core/serializers.py",
        (
            "class UserSerializer(serializers.ModelSerializer):\n"
            "    class Meta:\n"
            "        model = User\n"
            "        fields = ['id', 'about']\n"
            "        read_only_fields = []\n"
        ),
    )
    _write(
        tmp_path,
        "backend/core/views.py",
        (
            "class UserViewSet(viewsets.ModelViewSet):\n"
            "    serializer_class = UserSerializer\n"
            "    permission_classes = [permissions.IsAuthenticated]\n"
        ),
    )


def _react_parameters() -> dict[str, str]:
    return {
        "frontend_artifact_id": "frontend_app",
        "model_artifact_id": "user_model",
        "serializer_artifact_id": "user_serializer",
        "view_artifact_id": "user_views",
        "field": "about",
    }


def test_react_flow_capability_confirms_static_writable_unsanitized_flow(tmp_path) -> None:
    _write_react_flow_fixture(tmp_path)

    exit_code, stdout, stderr = worker._execute(
        _worker_payload(
            tmp_path,
            tool="inspect_react_dangerous_html_flow",
            parameters=_react_parameters(),
        )
    )

    payload = json.loads(stdout)
    assert exit_code == 0
    assert stderr == ""
    assert payload["verdict"] == "confirmed"
    assert payload["dangerous_html_sink_found"] is True
    assert payload["serializer_field_exposed"] is True
    assert payload["serializer_field_read_only"] is False
    assert payload["model_viewset_update_route"] is True
    assert payload["authentication_required"] is True
    assert payload["browser_execution_verified"] is False


def test_react_flow_capability_rejects_sanitized_sink_without_runtime_execution(tmp_path) -> None:
    _write_react_flow_fixture(tmp_path, sanitized=True)

    exit_code, stdout, stderr = worker._execute(
        _worker_payload(
            tmp_path,
            tool="inspect_react_dangerous_html_flow",
            parameters=_react_parameters(),
        )
    )

    payload = json.loads(stdout)
    assert exit_code == 0
    assert stderr == ""
    assert payload["verdict"] == "rejected"
    assert payload["sanitizer_detected"] is True
    assert payload["browser_execution_verified"] is False


def test_interpreter_turns_structured_source_result_into_verdict_evidence() -> None:
    action = ActionProposal(
        id="action-password-check",
        tool="inspect_python_password_assignment",
        target="sberlab-local",
        parameters={"artifact_id": "demo_seed"},
        purpose="Verify password assignment handling.",
        expected_evidence="Structured source evidence.",
    )
    execution = ExecutionResult(
        run_id="run-password-check",
        action_id=action.id,
        status="completed",
        exit_code=0,
        evidence_ref="execution-run-password-check",
        audit_ref="audit:run-password-check",
    )
    record = {
        "run_id": execution.run_id,
        "action_id": action.id,
        "status": "completed",
        "exit_code": 0,
        "stdout": json.dumps(
            {
                "schema": "cft.python_password_assignment_check.v1",
                "artifact_id": "demo_seed",
                "file": "backend/core/management/commands/seed_demo.py",
                "set_password_calls": 1,
                "validate_password_calls": 0,
                "hardcoded_password_literals": 1,
                "privileged_hardcoded_password_records": 1,
                "password_values_redacted": True,
                "verdict": "confirmed",
                "scope": "source",
                "runtime_auth_verified": False,
                "explanation": "Password assignment lacks validation.",
            }
        ),
    }

    evidence = build_evidence(
        action=action,
        execution=execution,
        record=record,
        evidence_loaded=True,
        artifact_refs=["artifact.json"],
        hypothesis_id="hypothesis-password-check",
    )

    assert evidence.type == "python_password_assignment_check"
    assert evidence.verdict == "confirmed"
    assert evidence.reliability == "high"
    assert evidence.observation.facts["password_values_redacted"] is True


def test_model_uses_capability_evidence_verdict_not_execution_success() -> None:
    model = DeterministicAgentModel()
    finding = _finding(
        rule_id="dockerfile.security.missing-user.missing-user",
        file="backend/Dockerfile",
        service="backend",
    )
    state = _state(finding)
    state["iteration_count"] = 1
    state["proposed_action"] = ActionProposal(
        id="action-docker-user-check",
        tool="inspect_dockerfile_user",
        target="sberlab-local",
        parameters={"artifact_id": "backend_dockerfile"},
        purpose="Verify Dockerfile condition.",
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
            source="static",
            hypothesis_id="hypothesis-docker-user-check",
            action=EvidenceAction(
                id="action-docker-user-check",
                tool="inspect_dockerfile_user",
                run_id="run-docker-user-check",
            ),
            observation=EvidenceObservation(
                kind="dockerfile_user_check",
                facts={"runtime_user_verified": False},
            ),
            scope=EvidenceScope(
                target="sberlab-local",
                environment="local",
                service="backend",
                description="source-only",
            ),
        )
    ]

    result = model.reevaluate(state)

    assert result.status == "confirmed"
    assert "structured Evidence" in result.explanation
