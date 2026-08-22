from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from schemas.target import TargetArtifact, TargetProfile, TargetRuntimeConfig


@dataclass(frozen=True)
class TargetArtifactDefinition:
    """Legacy constructor kept during TargetProfile migration."""

    id: str
    kind: str
    relative_path: str

    def __post_init__(self) -> None:
        artifact_id = self.id.strip()
        kind = self.kind.strip().lower()
        relative_path = self.relative_path.replace("\\", "/").strip()
        path = PurePosixPath(relative_path)

        if not artifact_id or any(char.isspace() for char in artifact_id):
            raise ValueError("Target artifact id must be a non-empty token")
        if not kind:
            raise ValueError("Target artifact kind is required")
        if not relative_path or path.is_absolute() or ".." in path.parts:
            raise ValueError("Target artifact path must stay inside the repository")

        object.__setattr__(self, "id", artifact_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "relative_path", path.as_posix())

    def to_profile_artifact(self) -> TargetArtifact:
        return TargetArtifact(
            id=self.id,
            kind=self.kind,
            relative_path=self.relative_path,
        )


@dataclass(frozen=True)
class TargetDefinition:
    """Legacy MVP target shape converted at the registry boundary."""

    id: str
    environment: str
    base_url: str
    repository_path: Path | None = None
    artifacts: dict[str, TargetArtifactDefinition] = field(default_factory=dict)

    def to_profile(self) -> TargetProfile:
        return TargetProfile(
            id=self.id,
            environment=self.environment,
            repository_path=self.repository_path,
            runtime=TargetRuntimeConfig(base_url=self.base_url),
            artifacts={
                artifact_id: artifact.to_profile_artifact()
                for artifact_id, artifact in self.artifacts.items()
            },
        )


class TargetRegistry:
    def __init__(self, targets: list[TargetProfile | TargetDefinition]) -> None:
        profiles = [
            target.to_profile() if isinstance(target, TargetDefinition) else target
            for target in targets
        ]
        self._targets = {target.id: target for target in profiles}
        if len(self._targets) != len(profiles):
            raise ValueError("Duplicate target id")

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        base_url_override: str | None = None,
        repository_path_override: str | Path | None = None,
    ) -> TargetRegistry:
        return cls(
            [
                TargetProfile.from_yaml(
                    path,
                    base_url_override=base_url_override,
                    repository_path_override=repository_path_override,
                )
            ]
        )

    def get(self, target_id: str) -> TargetProfile:
        try:
            return self._targets[target_id]
        except KeyError as exc:
            raise KeyError(f"Unknown executor target: {target_id}") from exc
