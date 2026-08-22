from __future__ import annotations

import json
from pathlib import Path

from discovery.service import ProjectDiscovery
from schemas.target import TargetProfile


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_discovery_does_not_map_folder_or_language_to_backend_frontend(tmp_path: Path) -> None:
    _write(tmp_path, "apps/payments/manage.py", "print('django entrypoint')\n")
    _write(tmp_path, "apps/payments/requirements.txt", "Django==5.1\n")
    _write(
        tmp_path,
        "apps/gateway/package.json",
        json.dumps(
            {
                "scripts": {"start": "node server.js"},
                "dependencies": {"express": "^5.0.0"},
            }
        ),
    )

    result = ProjectDiscovery().discover(tmp_path)

    assert [component.root for component in result.components] == [
        "apps/gateway",
        "apps/payments",
    ]
    by_root = {component.root: component for component in result.components}
    assert by_root["apps/payments"].frameworks == ["django"]
    assert by_root["apps/gateway"].frameworks == ["express"]
    assert {component.id for component in result.components}.isdisjoint({"backend", "frontend"})


def test_repository_name_does_not_change_discovery(tmp_path: Path) -> None:
    roots = [tmp_path / "first-name", tmp_path / "totally-different-name"]
    for root in roots:
        _write(
            root,
            "service/package.json",
            '{"scripts":{"dev":"vite"},"dependencies":{"vite":"1"}}',
        )
        _write(root, "service/vite.config.js", "export default {}\n")

    first = ProjectDiscovery().discover(roots[0])
    second = ProjectDiscovery().discover(roots[1])

    assert [component.model_dump() for component in first.components] == [
        component.model_dump() for component in second.components
    ]
    assert [signal.model_dump() for signal in first.signals] == [
        signal.model_dump() for signal in second.signals
    ]


def test_manifest_parent_does_not_create_duplicate_component(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.txt", "Django\n")
    _write(tmp_path, "backend/manage.py", "print('entrypoint')\n")
    _write(tmp_path, "backend/requirements.txt", "Django\n")
    _write(tmp_path, "backend/Dockerfile", "FROM python:3.12\n")

    result = ProjectDiscovery().discover(tmp_path)

    assert [component.root for component in result.components] == ["backend"]
    assert "requirements.txt" in result.project_files


def test_compose_build_context_binds_arbitrary_service_and_healthcheck(tmp_path: Path) -> None:
    _write(tmp_path, "services/payments/Dockerfile", "FROM python:3.12\n")
    _write(
        tmp_path,
        "compose.yml",
        """
services:
  payments-api:
    build:
      context: ./services/payments
    ports:
      - "127.0.0.1:8123:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:8000/ready/"]
""".strip(),
    )

    discovery = ProjectDiscovery()
    result = discovery.discover(tmp_path)
    profile = discovery.build_profile(result)

    assert len(result.components) == 1
    component = result.components[0]
    assert component.id == "payments-api"
    assert component.root == "services/payments"
    assert component.healthcheck_candidates[0].path == "/ready/"
    service = profile.services["payments-api"]
    assert service.compose_file == "compose.yml"
    assert service.compose_service == "payments-api"
    assert service.healthcheck is not None
    assert service.healthcheck.path == "/ready/"
    assert service.allowed_local_addresses == ["127.0.0.1:8123"]


def test_profile_builder_keeps_discovery_facts_separate_from_manual_overrides(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "app/manage.py", "print('entrypoint')\n")
    _write(tmp_path, "app/requirements.txt", "Django\n")
    _write(tmp_path, "app/Dockerfile", "FROM python:3.12\n")

    discovery = ProjectDiscovery()
    result = discovery.discover(tmp_path)
    base = TargetProfile.model_validate(
        {
            "id": "manual",
            "architecture": {"file": "targets/architecture.yaml"},
            "services": {
                "custom-api": {
                    "root": "app",
                    "type": "custom-framework-label",
                    "healthcheck": {"path": "/manual-health/"},
                }
            },
            "metadata": {"owner": "team-a"},
        }
    )

    profile = discovery.build_profile(result, base_profile=base)

    assert result.components[0].id == "app"
    assert list(profile.services) == ["custom-api"]
    service = profile.services["custom-api"]
    assert service.type == "custom-framework-label"
    assert service.dockerfile == "app/Dockerfile"
    assert service.healthcheck is not None
    assert service.healthcheck.path == "/manual-health/"
    assert profile.metadata["owner"] == "team-a"
    assert profile.metadata["discovery.version"] == "1"


def test_profile_builder_uses_technology_descriptors_not_architecture_roles(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "worker/package.json",
        json.dumps(
            {
                "scripts": {"start": "node worker.js"},
                "dependencies": {"@nestjs/core": "^11.0.0"},
            }
        ),
    )

    discovery = ProjectDiscovery()
    result = discovery.discover(tmp_path)
    profile = discovery.build_profile(result)

    service = next(iter(profile.services.values()))
    assert service.root == "worker"
    assert service.type == "nestjs"
    assert service.type not in {"backend", "frontend"}
