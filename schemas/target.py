from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urljoin, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TargetHealthcheck(BaseModel):
    path: str
    method: Literal["GET", "HEAD"] = "GET"
    expected_statuses: list[int] = Field(default_factory=lambda: [200])

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if (
            not value.startswith("/")
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or ".." in parsed.path.split("/")
        ):
            raise ValueError("Target healthcheck path must be a fixed absolute URL path")
        return value


class TargetService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str = "unknown"
    root: str = "."
    dependency_files: list[str] = Field(default_factory=list)
    dockerfile: str | None = None
    compose_file: str | None = None
    compose_service: str | None = None
    build: list[str] = Field(default_factory=list)
    run: list[str] = Field(default_factory=list)
    healthcheck: TargetHealthcheck | None = None
    allowed_local_addresses: list[str] = Field(default_factory=list)

    @field_validator("id", "type")
    @classmethod
    def validate_token(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Target service id/type cannot be empty")
        return value

    @field_validator("root", "dockerfile", "compose_file")
    @classmethod
    def validate_optional_relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_relative_path(value, allow_dot=True)

    @field_validator("dependency_files")
    @classmethod
    def validate_dependency_files(cls, values: list[str]) -> list[str]:
        return [_normalize_relative_path(value, allow_dot=False) for value in values]


class TargetArtifact(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str
    kind: str
    relative_path: str = Field(alias="path")
    roles: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        value = value.strip()
        if not value or any(char.isspace() for char in value):
            raise ValueError("Target artifact id must be a non-empty token")
        return value

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or any(char.isspace() for char in value):
            raise ValueError("Target artifact kind must be a non-empty token")
        return value

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _normalize_relative_path(value, allow_dot=False)

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Target artifact roles must be unique")
        return normalized

    def worker_payload(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.relative_path}


class TargetArchitectureConfig(BaseModel):
    file: Path | None = None


class TargetSASTConfig(BaseModel):
    adapter: str = "semgrep"


class TargetRuntimeConfig(BaseModel):
    type: str = "unknown"
    base_url: str | None = None
    allowed_local_addresses: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Target runtime.base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "Target runtime.base_url cannot contain credentials, query, or fragment"
            )
        return value.rstrip("/")


class TargetConstraints(BaseModel):
    external_network: bool = False
    host_filesystem: bool = False
    docker_socket: bool = False
    ci_secrets: bool = False


class TargetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""
    repository_path: Path | None = None
    environment: str = "local"
    architecture: TargetArchitectureConfig = Field(default_factory=TargetArchitectureConfig)
    sast: TargetSASTConfig = Field(default_factory=TargetSASTConfig)
    runtime: TargetRuntimeConfig = Field(default_factory=TargetRuntimeConfig)
    services: dict[str, TargetService] = Field(default_factory=dict)
    artifacts: dict[str, TargetArtifact] = Field(default_factory=dict)
    constraints: TargetConstraints = Field(default_factory=TargetConstraints)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_named_mappings(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)

        raw_services = normalized.get("services", {}) or {}
        if isinstance(raw_services, dict):
            services = {}
            for service_id, raw_service in raw_services.items():
                if isinstance(raw_service, TargetService):
                    services[str(service_id)] = raw_service
                    continue
                if not isinstance(raw_service, dict):
                    raise TypeError(f"Target service '{service_id}' must be an object")
                services[str(service_id)] = {"id": str(service_id), **raw_service}
            normalized["services"] = services

        raw_artifacts = normalized.get("artifacts", {}) or {}
        if isinstance(raw_artifacts, dict):
            artifacts = {}
            for artifact_id, raw_artifact in raw_artifacts.items():
                if isinstance(raw_artifact, TargetArtifact):
                    artifacts[str(artifact_id)] = raw_artifact
                    continue
                if not isinstance(raw_artifact, dict):
                    raise TypeError(f"Target artifact '{artifact_id}' must be an object")
                artifacts[str(artifact_id)] = {"id": str(artifact_id), **raw_artifact}
            normalized["artifacts"] = artifacts

        raw_health = normalized.pop("health", None)
        if isinstance(raw_health, dict) and isinstance(normalized.get("services"), dict):
            services = dict(normalized["services"])
            for service_id, health_path in raw_health.items():
                if service_id in services and "healthcheck" not in services[service_id]:
                    services[service_id] = {
                        **services[service_id],
                        "healthcheck": {"path": str(health_path)},
                    }
            normalized["services"] = services

        return normalized

    @field_validator("id", "environment")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Target profile id/environment cannot be empty")
        return value

    @field_validator("repository_path")
    @classmethod
    def normalize_repository_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def validate_mapping_keys(self) -> TargetProfile:
        if any(key != service.id for key, service in self.services.items()):
            raise ValueError("Target service mapping key must match service id")
        if any(key != artifact.id for key, artifact in self.artifacts.items()):
            raise ValueError("Target artifact mapping key must match artifact id")
        roots = [service.root for service in self.services.values()]
        if len(set(roots)) != len(roots):
            raise ValueError("Target service roots must be unique")
        return self

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        repository_path_override: str | Path | None = None,
        base_url_override: str | None = None,
    ) -> TargetProfile:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        profile = cls.model_validate(data)

        updates = {}
        if repository_path_override is not None:
            updates["repository_path"] = Path(repository_path_override).expanduser().resolve()
        if base_url_override is not None:
            updates["runtime"] = profile.runtime.model_copy(
                update={"base_url": TargetRuntimeConfig(base_url=base_url_override).base_url}
            )
        return profile.model_copy(update=updates)

    @property
    def base_url(self) -> str:
        if not self.runtime.base_url:
            raise ValueError(f"Target '{self.id}' has no runtime.base_url")
        return self.runtime.base_url

    def resolve_service(self, file_path: str) -> str | None:
        normalized = _normalize_relative_path(file_path, allow_dot=False)
        candidates: list[tuple[int, str]] = []
        for service_id, service in self.services.items():
            root = service.root
            if root == ".":
                candidates.append((0, service_id))
                continue
            if normalized == root or normalized.startswith(f"{root}/"):
                candidates.append((len(PurePosixPath(root).parts), service_id))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def artifact_id_for_path(self, file_path: str, *, kind: str | None = None) -> str:
        normalized = _normalize_relative_path(file_path, allow_dot=False)
        matches = [
            artifact.id
            for artifact in self.artifacts.values()
            if artifact.relative_path == normalized
            and (kind is None or artifact.kind.lower() == kind.lower())
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one trusted artifact for path '{normalized}', found {len(matches)}"
            )
        return matches[0]

    def artifact_id_for_role(self, role: str) -> str:
        matches = [
            artifact.id for artifact in self.artifacts.values() if role in artifact.roles
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one trusted artifact for role '{role}', found {len(matches)}"
            )
        return matches[0]

    def build_url(self, path: str) -> str:
        parsed_path = urlsplit(path)
        if (
            not path.startswith("/")
            or parsed_path.scheme
            or parsed_path.netloc
            or parsed_path.query
            or parsed_path.fragment
            or ".." in parsed_path.path.split("/")
        ):
            raise ValueError("Capability path must be a fixed absolute URL path")
        return urljoin(f"{self.base_url.rstrip('/')}/", path.lstrip("/"))

    def worker_artifacts(self) -> dict[str, dict[str, str]]:
        return {
            artifact_id: artifact.worker_payload()
            for artifact_id, artifact in self.artifacts.items()
        }


def _normalize_relative_path(value: str, *, allow_dot: bool) -> str:
    normalized = value.replace("\\", "/").strip()
    if allow_dot and normalized == ".":
        return "."
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("Target path must stay inside the repository")
    return path.as_posix()
