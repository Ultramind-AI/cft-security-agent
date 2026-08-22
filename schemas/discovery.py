from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DiscoverySignalKind = Literal[
    "manifest",
    "technology",
    "framework",
    "component_anchor",
    "dockerfile",
    "compose_service",
    "build_command",
    "run_command",
    "healthcheck",
    "local_address",
]
DiscoveryCommandKind = Literal["build", "run"]


class DiscoverySignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector: str
    kind: DiscoverySignalKind
    path: str
    component_root: str | None = None
    name: str | None = None
    value: str | None = None
    command: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    anchor: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("detector")
    @classmethod
    def validate_detector(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Discovery detector cannot be empty")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_relative_path(value, allow_dot=False)

    @field_validator("component_root")
    @classmethod
    def validate_component_root(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_relative_path(value, allow_dot=True)

    @field_validator("command")
    @classmethod
    def validate_command(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("Discovery command tokens cannot be empty")
        return normalized


class DiscoveryCommandCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DiscoveryCommandKind
    command: list[str]
    source_path: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return _normalize_relative_path(value, allow_dot=False)

    @field_validator("command")
    @classmethod
    def validate_command(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if not normalized or any(not value for value in normalized):
            raise ValueError("Discovery command candidate cannot be empty")
        return normalized


class DiscoveryHealthcheckCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    source_path: str
    method: Literal["GET", "HEAD"] = "GET"
    expected_statuses: list[int] = Field(default_factory=lambda: [200])
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("path")
    @classmethod
    def validate_health_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/") or ".." in PurePosixPath(value).parts:
            raise ValueError("Discovery healthcheck must be an absolute local path")
        return value

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return _normalize_relative_path(value, allow_dot=False)


class DiscoveryComposeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compose_file: str
    service: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("compose_file")
    @classmethod
    def validate_compose_file(cls, value: str) -> str:
        return _normalize_relative_path(value, allow_dot=False)

    @field_validator("service")
    @classmethod
    def validate_service(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Compose service name cannot be empty")
        return value


class DiscoveredComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    root: str
    technologies: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    dockerfiles: list[str] = Field(default_factory=list)
    compose_candidates: list[DiscoveryComposeCandidate] = Field(default_factory=list)
    build_candidates: list[DiscoveryCommandCandidate] = Field(default_factory=list)
    run_candidates: list[DiscoveryCommandCandidate] = Field(default_factory=list)
    healthcheck_candidates: list[DiscoveryHealthcheckCandidate] = Field(default_factory=list)
    allowed_local_addresses: list[str] = Field(default_factory=list)
    source_paths: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Discovered component id cannot be empty")
        return value

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        return _normalize_relative_path(value, allow_dot=True)


class ProjectDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_root: Path
    components: list[DiscoveredComponent] = Field(default_factory=list)
    signals: list[DiscoverySignal] = Field(default_factory=list)
    project_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("repository_root")
    @classmethod
    def normalize_repository_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("project_files")
    @classmethod
    def validate_project_files(cls, values: list[str]) -> list[str]:
        return [_normalize_relative_path(value, allow_dot=False) for value in values]

    def resolve_component(self, file_path: str) -> str | None:
        normalized = _normalize_relative_path(file_path, allow_dot=False)
        candidates: list[tuple[int, str]] = []
        for component in self.components:
            if component.root == ".":
                candidates.append((0, component.id))
            elif normalized == component.root or normalized.startswith(f"{component.root}/"):
                candidates.append((len(PurePosixPath(component.root).parts), component.id))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]


def _normalize_relative_path(value: str, *, allow_dot: bool) -> str:
    normalized = value.replace("\\", "/").strip()
    if allow_dot and normalized == ".":
        return "."
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("Discovery path must stay inside the repository")
    return path.as_posix()
