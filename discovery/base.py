from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from schemas.discovery import DiscoverySignal

IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "vendor",
    }
)
MAX_DISCOVERY_FILE_BYTES = 1_048_576


@dataclass(frozen=True)
class DiscoveryContext:
    root: Path
    files: tuple[str, ...]

    @classmethod
    def scan(cls, repository_root: str | Path) -> DiscoveryContext:
        root = Path(repository_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Discovery repository root does not exist: {root}")

        files: list[str] = []
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            dirnames[:] = sorted(
                dirname
                for dirname in dirnames
                if dirname not in IGNORED_DIRECTORIES
                and not (current_path / dirname).is_symlink()
            )
            for filename in sorted(filenames):
                path = current_path / filename
                if path.is_symlink() or not path.is_file():
                    continue
                files.append(path.relative_to(root).as_posix())
        return cls(root=root, files=tuple(files))

    def read_text(self, relative_path: str) -> str:
        path = (self.root / relative_path).resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or path.is_symlink():
            raise ValueError(f"Discovery path is outside repository: {relative_path}")
        if path.stat().st_size > MAX_DISCOVERY_FILE_BYTES:
            raise ValueError(f"Discovery file is too large: {relative_path}")
        return path.read_text(encoding="utf-8-sig", errors="replace")


class ProjectDetector(Protocol):
    name: str

    def detect(self, context: DiscoveryContext) -> list[DiscoverySignal]: ...
