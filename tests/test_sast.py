from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sast.normalizer import normalize_semgrep_payload, normalize_semgrep_result
from sast.semgrep_runner import SemgrepError, run_semgrep_scan


def _raw_finding(path: str = "backend/core/views.py") -> dict:
    return {
        "check_id": "python.django.demo-rule",
        "path": path,
        "start": {"line": 12, "col": 1},
        "end": {"line": 12, "col": 10},
        "extra": {
            "message": "Example security finding",
            "severity": "WARNING",
        },
    }


def test_normalizer_creates_stable_finding_and_service() -> None:
    finding = normalize_semgrep_result(_raw_finding())

    assert finding.source == "semgrep"
    assert finding.rule_id == "python.django.demo-rule"
    assert finding.id == "python.django.demo-rule:backend/core/views.py:12"
    assert finding.service == "backend"
    assert finding.line_start == 12
    assert finding.severity == "WARNING"


def test_normalizer_detects_frontend_component() -> None:
    finding = normalize_semgrep_result(_raw_finding("frontend/src/App.jsx"))
    assert finding.service == "frontend"


def test_normalize_payload_requires_results_list() -> None:
    with pytest.raises(ValueError, match="results"):
        normalize_semgrep_payload({"results": {}})


def test_runner_uses_local_target_as_cwd(monkeypatch, tmp_path: Path) -> None:
    payload = {"results": [_raw_finding()], "errors": []}
    captured: dict = {}

    monkeypatch.setattr("sast.semgrep_runner.shutil.which", lambda _: "semgrep")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr("sast.semgrep_runner.subprocess.run", fake_run)

    result = run_semgrep_scan(tmp_path)

    assert captured["command"][:2] == ["semgrep", "scan"]
    assert captured["command"][-1] == "."
    assert captured["kwargs"]["cwd"] == tmp_path.resolve()
    assert captured["kwargs"]["shell"] is False
    assert len(result.findings) == 1


def test_runner_explains_missing_semgrep(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sast.semgrep_runner.shutil.which", lambda _: None)

    with pytest.raises(SemgrepError, match="SAST extra"):
        run_semgrep_scan(tmp_path)
