from pathlib import Path

import yaml

from architecture.context import ProjectDescriptionAdapter
from schemas.architecture import (
    ArchitectureContext,
    ArchitectureOverrides,
    ProjectDescription,
)


class ArchitectureService:
    """YAML adapter для описаний проекта и необязательных overrides оператора"""

    def __init__(
        self,
        path: str | Path,
        *,
        overrides_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        description = ProjectDescription.model_validate(self.data)

        if overrides_path is None:
            override_data = {}
            self.overrides_path = None
        else:
            self.overrides_path = Path(overrides_path)
            override_data = (
                yaml.safe_load(self.overrides_path.read_text(encoding="utf-8")) or {}
            )

        overrides = ArchitectureOverrides.model_validate(override_data)
        self._provider = ProjectDescriptionAdapter(description, overrides)

    def get_context(self, service: str) -> ArchitectureContext:
        return self._provider.get_context(service)
