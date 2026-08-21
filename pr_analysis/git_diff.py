from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class GitDiffError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitDiff:
    changed_files: list[str] = field(default_factory=list)
    changed_lines: dict[str, list[int]] = field(default_factory=dict)


def read_git_diff(repository: str | Path, *, base_ref: str, head_ref: str) -> GitDiff:
    root = Path(repository).expanduser().resolve()
    if not (root / ".git").exists():
        raise GitDiffError(f"Target is not a Git repository: {root}")

    command = [
        "git",
        "-C",
        str(root),
        "diff",
        "--unified=0",
        "--no-renames",
        f"{base_ref}...{head_ref}",
        "--",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise GitDiffError(f"git diff failed: {detail[:2000]}")

    return parse_git_diff(completed.stdout)


def parse_git_diff(text: str) -> GitDiff:
    changed_files: list[str] = []
    changed_lines: dict[str, list[int]] = {}
    current_file: str | None = None

    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = shlex.split(line)
            current_file = _normalize_path(parts[-1]) if len(parts) >= 4 else None
            if current_file is not None and current_file not in changed_files:
                changed_files.append(current_file)
                changed_lines.setdefault(current_file, [])
            continue
        if line.startswith("+++ "):
            value = line[4:]
            if value != "/dev/null":
                current_file = _normalize_path(value)
            if current_file is not None and current_file not in changed_files:
                changed_files.append(current_file)
                changed_lines.setdefault(current_file, [])
            continue

        match = _HUNK_HEADER.match(line)
        if match is None or current_file is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        changed_lines[current_file].extend(range(start, start + count))

    return GitDiff(
        changed_files=changed_files,
        changed_lines={path: sorted(set(lines)) for path, lines in changed_lines.items()},
    )


def _normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    return normalized[2:] if normalized.startswith("b/") else normalized
