import pytest

from app.config import settings


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
