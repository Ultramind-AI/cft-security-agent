from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import yaml


@dataclass(frozen=True)
class TargetDefinition:
    id: str
    environment: str
    base_url: str
    repository_path: Path | None = None

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

        return cls(
            [
                TargetDefinition(
                    id=str(data["id"]),
                    environment=str(data.get("environment", "unknown")),
                    base_url=str(base_url),
                    repository_path=repository_path,
                )
            ]
        )

    def get(self, target_id: str) -> TargetDefinition:
        try:
            return self._targets[target_id]
        except KeyError as exc:
            raise KeyError(f"Unknown executor target: {target_id}") from exc
