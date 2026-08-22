import pytest

from executor.targets import TargetRegistry
from schemas.target import TargetProfile


def test_sberlab_profile_resolves_services_and_artifacts() -> None:
    profile = TargetProfile.from_yaml("targets/sberlab.yaml")

    assert profile.resolve_service("backend/core/views.py") == "backend"
    assert profile.resolve_service("frontend\\frontend\\src\\App.jsx") == "frontend"
    assert profile.artifact_id_for_path("backend/Dockerfile", kind="dockerfile") == (
        "backend_dockerfile"
    )
    assert profile.artifact_id_for_role("react_html_flow.model") == "user_model"
    assert profile.metadata["react_html_flow.field"] == "about"


def test_profile_supports_second_project_layout(tmp_path) -> None:
    profile_path = tmp_path / "second-target.yaml"
    profile_path.write_text(
        """id: second-local
name: Second target
environment: sandbox
repository_path: ./second
architecture:
  file: ./architecture.yaml
runtime:
  base_url: http://127.0.0.1:9100
services:
  api:
    type: django
    root: src/server
    dependency_files:
      - src/server/requirements.txt
    dockerfile: deploy/api.Dockerfile
  web:
    type: react
    root: ui
    dependency_files:
      - ui/package.json
artifacts:
  api_dockerfile:
    kind: dockerfile
    path: deploy/api.Dockerfile
""",
        encoding="utf-8",
    )

    profile = TargetProfile.from_yaml(profile_path)

    assert profile.id == "second-local"
    assert profile.resolve_service("src/server/app/views.py") == "api"
    assert profile.resolve_service("ui/src/App.tsx") == "web"
    assert profile.resolve_service("README.md") is None
    assert profile.artifact_id_for_path("deploy/api.Dockerfile") == "api_dockerfile"


def test_target_registry_uses_target_profile_contract(tmp_path) -> None:
    profile = TargetProfile.from_yaml(
        "targets/sberlab.yaml",
        repository_path_override=tmp_path / "target",
    )
    registry = TargetRegistry([profile])

    loaded = registry.get(profile.id)

    assert loaded is profile
    assert loaded.repository_path == (tmp_path / "target").resolve()
    assert loaded.base_url == "http://127.0.0.1:8000"


def test_profile_rejects_repository_path_escape_in_artifact() -> None:
    with pytest.raises(ValueError, match="inside the repository"):
        TargetProfile.model_validate(
            {
                "id": "bad",
                "runtime": {"base_url": "http://127.0.0.1:9300"},
                "artifacts": {
                    "bad": {"kind": "python", "path": "../secret.py"},
                },
            }
        )
