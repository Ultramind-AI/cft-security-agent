import json

import pytest

from app.e2e_inputs import build_real_initial_state
from sast.repository import JsonFindingRepository
from tools.runtime import LocalCodeReader


def _write_findings(path) -> str:
    finding_id = "docker.rule:backend\\Dockerfile:4"
    path.write_text(
        json.dumps(
            [
                {
                    "id": finding_id,
                    "source": "semgrep",
                    "rule_id": "docker.rule",
                    "title": "Container hardening finding",
                    "description": "Synthetic normalized finding shape for integration tests.",
                    "file": "backend\\Dockerfile",
                    "line_start": 4,
                    "line_end": 4,
                    "severity": "ERROR",
                    "service": "backend",
                }
            ]
        ),
        encoding="utf-8",
    )
    return finding_id


def _write_architecture(path) -> None:
    path.write_text(
        """services:
  backend:
    type: api
    public: true
    criticality: high
    trust_zone: application
    connects_to:
      - database
  database:
    type: database
    public: false
    criticality: critical
    connects_to: []
""",
        encoding="utf-8",
    )


def test_json_finding_repository_loads_normalized_finding(tmp_path) -> None:
    findings_path = tmp_path / "findings.json"
    finding_id = _write_findings(findings_path)

    finding = JsonFindingRepository(findings_path).get_finding(finding_id)

    assert finding.id == finding_id
    assert finding.file == "backend\\Dockerfile"
    assert finding.service == "backend"


def test_local_code_reader_normalizes_semgrep_windows_path(tmp_path) -> None:
    target = tmp_path / "target"
    dockerfile = target / "backend" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM python\nWORKDIR /app\nCOPY . .\nCMD run\n", encoding="utf-8")

    result = LocalCodeReader(target).read_code(
        "backend\\Dockerfile",
        4,
        4,
        context_lines=1,
    )

    assert result.line_start == 3
    assert result.line_end == 4
    assert "    4: CMD run" in result.content


def test_local_code_reader_fails_closed_on_path_escape(tmp_path) -> None:
    with pytest.raises(ValueError, match="relative path"):
        LocalCodeReader(tmp_path).read_code("../outside.txt", 1, 1)


def test_build_real_initial_state_uses_real_code_and_architecture(tmp_path) -> None:
    findings_path = tmp_path / "findings.json"
    finding_id = _write_findings(findings_path)

    target = tmp_path / "target"
    dockerfile = target / "backend" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text(
        "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nCMD run\n",
        encoding="utf-8",
    )

    architecture_path = tmp_path / "architecture.yaml"
    _write_architecture(architecture_path)

    state = build_real_initial_state(
        findings_path=findings_path,
        target_root=target,
        architecture_path=architecture_path,
        finding_id=finding_id,
    )

    assert state["finding"].id == finding_id
    assert "FROM python:3.11-slim" in state["code_context"]
    assert state["architecture_context"].service == "backend"
    assert state["architecture_context"].public_exposure is True
    assert state["architecture_context"].databases == ["database"]
    assert state["max_iterations"] == 1
