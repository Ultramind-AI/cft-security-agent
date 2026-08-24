from pathlib import Path

import pytest

from app.config import settings
from schemas.target import TargetProfile

_TARGET_MANIFESTS = (
    Path(__file__).resolve().parents[1] / "targets" / "sberlab.yaml",
    Path(__file__).resolve().parents[1] / "targets" / "autodealer.yaml",
)


@pytest.fixture(params=_TARGET_MANIFESTS, ids=("sberlab", "autodealer"))
def registered_target_profile(request) -> TargetProfile:
    return TargetProfile.from_yaml(request.param)


@pytest.fixture(autouse=True)
def isolated_executor_runtime(tmp_path, monkeypatch):
    runtime = tmp_path / "executor_data"
    monkeypatch.setattr(settings, "executor_work_dir", runtime / "workspaces")
    monkeypatch.setattr(settings, "evidence_dir", runtime / "evidence")
    monkeypatch.setattr(
        settings,
        "executor_audit_log",
        runtime / "audit" / "executor.jsonl",
    )
