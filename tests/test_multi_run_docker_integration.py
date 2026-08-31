"""Проверка отмены очереди и teardown на реальном Docker"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from threading import Event

import pytest

from api.registry import ApiTargetRegistry, RegisteredTarget
from api.scheduler import ResourceBudget, ResourceRequest
from api.service import RunOrchestrator
from api.store import ApiStore
from executor.sandbox_manager import SandboxManager
from schemas.api import CreateRunRequest
from schemas.target import TargetProfile


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5, check=False
        ).returncode == 0
    except OSError:
        return False


def _resources(project: str, kind: str) -> list[str]:
    base = {
        "container": ["docker", "ps", "--all", "--quiet"],
        "network": ["docker", "network", "ls", "--quiet"],
        "volume": ["docker", "volume", "ls", "--quiet"],
    }[kind]
    result = subprocess.run(
        [*base, "--filter", f"label=com.docker.compose.project={project}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return result.stdout.splitlines()


@pytest.mark.integration
@pytest.mark.skipif(not _docker_ready(), reason="requires a working Docker daemon")
def test_cancelled_run_releases_budget_and_compose_resources(tmp_path: Path) -> None:
    raw_target = os.getenv("SBERLAB_TARGET_PATH")
    if not raw_target:
        pytest.skip("Set SBERLAB_TARGET_PATH to run lifecycle integration")
    repository = Path(raw_target).resolve()
    if not repository.is_dir():
        pytest.skip("SBERLAB_TARGET_PATH is not a directory")

    profile_path = Path("targets/sberlab.yaml").resolve()
    profile = TargetProfile.from_yaml(
        profile_path,
        repository_path_override=repository,
    )
    registry = ApiTargetRegistry([RegisteredTarget(profile_path, profile)])
    started = Event()
    compose_projects: list[str] = []

    def managed_pipeline(args) -> int:
        session = SandboxManager(readiness_timeout=90).open(profile)
        with session:
            state = session.collect_state()
            compose_projects.append(str(state["compose_project"]))
            started.set()
            while True:
                args.cancellation_token.raise_if_cancelled()
                time.sleep(0.05)

    budget = ResourceBudget(sandboxes=1, cpu=1.0, memory_mb=256)
    service = RunOrchestrator(
        registry=registry,
        store=ApiStore(tmp_path / "api.sqlite3"),
        artifact_root=tmp_path / "artifacts",
        pipeline_runner=managed_pipeline,
        resource_budget=budget,
        resource_request=ResourceRequest(1, 1.0, 256),
        max_workers=1,
    )
    first = service.create_run(CreateRunRequest(target_id=profile.id))
    second = service.create_run(CreateRunRequest(target_id=profile.id))
    assert started.wait(120)
    assert service.get_run(second.id).status == "queued"
    assert service.cancel_run(second.id).status == "cancelled"
    assert service.cancel_run(first.id).status in {"cancelling", "cancelled"}

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and service.get_run(first.id).status != "cancelled":
        time.sleep(0.05)
    assert service.get_run(first.id).status == "cancelled"
    assert budget.used == ResourceRequest(0, 0.0, 0)
    assert compose_projects
    assert all(
        not _resources(compose_projects[0], kind)
        for kind in ("container", "network", "volume")
    )
    service.close()
