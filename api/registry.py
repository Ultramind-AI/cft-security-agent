from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from schemas.target import TargetProfile


@dataclass(frozen=True)
class RegisteredTarget:
    profile_path: Path
    profile: TargetProfile


class ApiTargetRegistry:
    """Trusted TargetProfile registry used by the service API."""

    def __init__(self, targets: list[RegisteredTarget]) -> None:
        self._targets = {target.profile.id: target for target in targets}
        if len(self._targets) != len(targets):
            raise ValueError("Duplicate API target id")

    @classmethod
    def from_profile_paths(
        cls,
        paths: list[str | Path],
        *,
        trusted_root: str | Path,
    ) -> ApiTargetRegistry:
        root = Path(trusted_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("API trusted target root must be a directory")

        targets: list[RegisteredTarget] = []
        for value in paths:
            path = Path(value).expanduser().resolve(strict=True)
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    "API target profiles must stay under the trusted target root"
                ) from exc
            if not path.is_file():
                raise ValueError("API target profile must be a YAML file")
            profile = TargetProfile.from_yaml(path)
            targets.append(RegisteredTarget(profile_path=path, profile=profile))
        if not targets:
            raise ValueError("At least one API target profile must be registered")
        return cls(targets)

    def get(self, target_id: str) -> RegisteredTarget:
        try:
            return self._targets[target_id]
        except KeyError as exc:
            raise KeyError(f"Unknown API target: {target_id}") from exc

    def list(self) -> list[RegisteredTarget]:
        return [self._targets[target_id] for target_id in sorted(self._targets)]
