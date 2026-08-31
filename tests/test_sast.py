from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sast.normalizer import normalize_semgrep_payload, normalize_semgrep_result
from sast.semgrep_runner import SemgrepError, run_semgrep_scan
from schemas.target import TargetProfile


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


def test_normalizer_creates_stable_finding_without_architecture_guess() -> None:
    finding = normalize_semgrep_result(_raw_finding())

    assert finding.source == "semgrep"
    assert finding.rule_id == "python.django.demo-rule"
    assert finding.id == "python.django.demo-rule:backend/core/views.py:12"
    assert finding.service is None
    assert finding.line_start == 12
    assert finding.severity == "WARNING"


def test_normalizer_uses_target_profile_service_mapping() -> None:
    profile = TargetProfile.from_yaml("targets/sberlab.yaml")

    backend = normalize_semgrep_result(
        _raw_finding(),
        service_resolver=profile.resolve_service,
    )
    frontend = normalize_semgrep_result(
        _raw_finding("frontend\\frontend\\src\\App.jsx"),
        service_resolver=profile.resolve_service,
    )

    assert backend.service == "backend"
    assert frontend.service == "frontend"


def test_normalize_payload_requires_results_list() -> None:
    with pytest.raises(TypeError, match="results"):
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

    monkeypatch.setattr("sast.semgrep_runner.run_cancellable_process", fake_run)

    result = run_semgrep_scan(tmp_path)

    assert captured["command"][:2] == ["semgrep", "scan"]
    assert captured["command"][-1] == "."
    assert captured["kwargs"]["cwd"] == tmp_path.resolve()
    # run_cancellable_process сам всегда использует shell=False.
    assert "shell" not in captured["kwargs"]
    assert len(result.findings) == 1


def test_runner_explains_missing_semgrep(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sast.semgrep_runner.shutil.which", lambda _: None)
    monkeypatch.setattr("sast.semgrep_runner._environment_semgrep", lambda: None)

    with pytest.raises(SemgrepError, match="SAST extra"):
        run_semgrep_scan(tmp_path)


def test_runner_falls_back_to_isolated_docker_after_windows_core_failure(
    monkeypatch, tmp_path: Path
) -> None:
    payload = {"results": [_raw_finding()], "errors": []}
    calls: list[list[str]] = []
    monkeypatch.setattr("sast.semgrep_runner.sys.platform", "win32")
    monkeypatch.setattr(
        "sast.semgrep_runner.shutil.which",
        lambda name: "semgrep" if name == "semgrep" else "docker",
    )

    def fake_run(command, **_kwargs):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=2,
                stdout="",
                stderr="semgrep-core rule validation failed",
            )
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("sast.semgrep_runner.run_cancellable_process", fake_run)

    result = run_semgrep_scan(tmp_path)

    assert result.findings
    assert calls[1][:4] == ["docker", "run", "--rm", "--read-only"]
    assert any("/src:ro" in item for item in calls[1])
