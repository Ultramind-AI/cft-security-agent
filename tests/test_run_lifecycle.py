from __future__ import annotations

import argparse
import time
from pathlib import Path
from threading import Event

from api.registry import ApiTargetRegistry
from api.scheduler import ResourceBudget, ResourceRequest
from api.service import RunOrchestrator
from api.store import ApiStore
from pipeline.cancellation import CancellationToken
from schemas.api import CreateRunRequest
from schemas.pipeline import GateResult


def _registry(tmp_path: Path) -> ApiTargetRegistry:
    repository = tmp_path / "repository"
    repository.mkdir(exist_ok=True)
    architecture = tmp_path / "architecture.yaml"
    architecture.write_text("services: {}\n", encoding="utf-8")
    targets = tmp_path / "targets"
    targets.mkdir(exist_ok=True)
    profile = targets / "demo.yaml"
    profile.write_text(
        "\n".join(
            [
                "id: demo",
                "environment: local",
                f"repository_path: {repository}",
                "architecture:",
                f"  file: {architecture}",
                "services: {}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return ApiTargetRegistry.from_profile_paths([profile], trusted_root=targets)


def _write_gate(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    gate = GateResult(
        decision="pass",
        exit_code=0,
        decision_basis="no_blocking_condition",
    )
    (output / "gate.json").write_text(gate.model_dump_json(), encoding="utf-8")
    return 0


def _wait_status(store: ApiStore, run_id: str, expected: set[str]) -> str:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = store.get_run(run_id).status
        if status in expected:
            return status
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach {expected}")


def test_running_cancel_releases_budget_before_terminal_state(tmp_path: Path) -> None:
    started = Event()

    def blocking(args: argparse.Namespace) -> int:
        token: CancellationToken = args.cancellation_token
        started.set()
        while not token.is_cancelled():
            time.sleep(0.005)
        token.raise_if_cancelled()
        raise AssertionError("unreachable")

    store = ApiStore(tmp_path / "api.sqlite3")
    budget = ResourceBudget(sandboxes=1, cpu=1.0, memory_mb=128)
    service = RunOrchestrator(
        registry=_registry(tmp_path),
        store=store,
        artifact_root=tmp_path / "artifacts",
        pipeline_runner=blocking,
        resource_budget=budget,
        resource_request=ResourceRequest(1, 1.0, 128),
        max_workers=1,
    )
    run = service.create_run(CreateRunRequest(target_id="demo"))
    assert started.wait(2)
    assert service.cancel_run(run.id).status == "cancelling"
    assert _wait_status(store, run.id, {"cancelled"}) == "cancelled"
    assert store.get_run(run.id).exit_code == 130
    assert budget.used == ResourceRequest(0, 0.0, 0)
    service.close()


def test_second_run_stays_queued_and_can_be_cancelled(tmp_path: Path) -> None:
    started = Event()
    release = Event()

    def pipeline(args: argparse.Namespace) -> int:
        started.set()
        while not release.is_set():
            args.cancellation_token.raise_if_cancelled()
            time.sleep(0.005)
        return _write_gate(args)

    store = ApiStore(tmp_path / "api.sqlite3")
    service = RunOrchestrator(
        registry=_registry(tmp_path),
        store=store,
        artifact_root=tmp_path / "artifacts",
        pipeline_runner=pipeline,
        max_workers=1,
    )
    one = service.create_run(CreateRunRequest(target_id="demo"))
    two = service.create_run(CreateRunRequest(target_id="demo"))
    assert started.wait(2)
    assert store.get_run(one.id).status == "running"
    assert store.get_run(two.id).status == "queued"
    assert service.cancel_run(two.id).status == "cancelled"
    release.set()
    assert _wait_status(store, one.id, {"completed"}) == "completed"
    service.close()


def test_startup_recovery_marks_active_and_restores_queued(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    store = ApiStore(tmp_path / "api.sqlite3")
    target = registry.get("demo")
    store.upsert_project(profile_path=target.profile_path, profile=target.profile)
    active = store.create_run(
        run_id="run-active",
        target_id="demo",
        agent_mode="stub",
        max_iterations=1,
        artifact_dir=tmp_path / "active",
    )
    assert store.mark_running(active.id)
    queued = store.create_run(
        run_id="run-queued",
        target_id="demo",
        agent_mode="stub",
        max_iterations=1,
        artifact_dir=tmp_path / "queued",
    )
    service = RunOrchestrator(
        registry=registry,
        store=store,
        artifact_root=tmp_path / "artifacts",
        pipeline_runner=_write_gate,
        max_workers=1,
    )
    assert store.get_run(active.id).status == "technical_failure"
    assert _wait_status(store, queued.id, {"completed"}) == "completed"
    service.close()
