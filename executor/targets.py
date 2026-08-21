from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

import yaml


@dataclass(frozen=True)
class TargetArtifactDefinition:
    id: str
    kind: str
    relative_path: str

    def __post_init__(self) -> None:
        artifact_id = self.id.strip()
        kind = self.kind.strip().lower()
        relative_path = self.relative_path.replace("\\", "/").strip()
        path = PurePosixPath(relative_path)

        # Путь артефакта принадлежит профилю цели, а не агенту
        if not artifact_id or any(char.isspace() for char in artifact_id):
            raise ValueError("Target artifact id must be a non-empty token")
        if not kind:
            raise ValueError("Target artifact kind is required")
        if not relative_path or path.is_absolute() or ".." in path.parts:
            raise ValueError("Target artifact path must stay inside the repository")

        object.__setattr__(self, "id", artifact_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "relative_path", path.as_posix())

    def worker_payload(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "path": self.relative_path,
        }


@dataclass(frozen=True)
class TargetDefinition:
    id: str
    environment: str
    base_url: str
    repository_path: Path | None = None
    compose_file: Path | None = None
    health_path: str = "/health/"
    artifacts: dict[str, TargetArtifactDefinition] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Target base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Target base_url cannot contain credentials, query, or fragment")
        if self.repository_path is not None:
            object.__setattr__(
                self,
                "repository_path",
                Path(self.repository_path).expanduser().resolve(),
            )
        if self.compose_file is not None:
            compose_file = Path(self.compose_file).expanduser().resolve()
            if (
                self.repository_path is None
                or not compose_file.is_relative_to(self.repository_path)
            ):
                raise ValueError("Target compose_file must stay inside the repository")
            object.__setattr__(self, "compose_file", compose_file)
        parsed_health_path = urlsplit(self.health_path)
        if (
            not self.health_path.startswith("/")
            or parsed_health_path.scheme
            or parsed_health_path.netloc
            or parsed_health_path.query
            or parsed_health_path.fragment
            or ".." in parsed_health_path.path.split("/")
        ):
            raise ValueError("Target health_path must be a fixed absolute URL path")
        if any(key != artifact.id for key, artifact in self.artifacts.items()):
            raise ValueError("Target artifact mapping key must match artifact id")

    def build_url(self, path: str) -> str:
        # Capability получает только фиксированный endpoint без URL-компонентов
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


class TargetRegistry:
    def __init__(self, targets: list[TargetDefinition]) -> None:
        self._targets = {target.id: target for target in targets}
        if len(self._targets) != len(targets):
            raise ValueError("Duplicate target id")

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        base_url_override: str | None = None,
        repository_path_override: str | Path | None = None,
    ) -> "TargetRegistry":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        runtime = data.get("runtime", {})
        base_url = base_url_override or runtime.get("base_url")

        if not base_url:
            raise ValueError("Target runtime.base_url is required")

        configured_repository = repository_path_override or data.get("repository_path")
        repository_path = (
            Path(configured_repository).expanduser().resolve()
            if configured_repository
            else None
        )

        raw_artifacts = data.get("artifacts", {}) or {}
        if not isinstance(raw_artifacts, dict):
            raise TypeError("Target artifacts must be a mapping")
        artifacts: dict[str, TargetArtifactDefinition] = {}
        for artifact_id, raw_definition in raw_artifacts.items():
            if not isinstance(raw_definition, dict):
                raise TypeError(f"Target artifact '{artifact_id}' must be an object")
            artifacts[str(artifact_id)] = TargetArtifactDefinition(
                id=str(artifact_id),
                kind=str(raw_definition.get("kind", "")),
                relative_path=str(raw_definition.get("path", "")),
            )

        compose_file: Path | None = None
        compose_value = runtime.get("compose_file")
        if compose_value:
            if repository_path is None:
                raise ValueError("runtime.compose_file requires repository_path")
            compose_file = (repository_path / str(compose_value)).resolve()

        return cls(
            [
                TargetDefinition(
                    id=str(data["id"]),
                    environment=str(data.get("environment", "unknown")),
                    base_url=str(base_url),
                    repository_path=repository_path,
                    compose_file=compose_file,
                    health_path=str(data.get("health", {}).get("backend", "/health/")),
                    artifacts=artifacts,
                )
            ]
        )

    def get(self, target_id: str) -> TargetDefinition:
        try:
            return self._targets[target_id]
        except KeyError as exc:
            raise KeyError(f"Unknown executor target: {target_id}") from exc
