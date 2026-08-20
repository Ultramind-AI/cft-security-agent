from __future__ import annotations

from pathlib import Path, PurePosixPath

from tools.contracts import CodeContextResult


class LocalCodeReader:
    """Читает ограниченное окно исходного кода из явно настроенного корня цели."""

    def __init__(self, target_root: str | Path):
        self.target_root = Path(target_root).resolve()

    def read_code(
        self,
        file: str,
        line_start: int | None,
        line_end: int | None,
        *,
        context_lines: int = 8,
    ) -> CodeContextResult:
        if context_lines < 0 or context_lines > 50:
            raise ValueError("context_lines must be between 0 and 50")

        # Чтение ограничено корнем target; абсолютные и traversal-пути отбрасываются
        relative = _normalise_relative_path(file)
        candidate = (self.target_root / Path(*relative.parts)).resolve()

        try:
            candidate.relative_to(self.target_root)
        except ValueError as exc:
            raise ValueError("Requested source file escapes configured target root") from exc

        if not candidate.is_file():
            raise FileNotFoundError(f"Source file not found inside target: {file}")

        lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        if not lines:
            return CodeContextResult(
                file=file,
                line_start=None,
                line_end=None,
                content="",
            )

        requested_start = line_start or 1
        requested_end = line_end or requested_start
        if requested_start < 1 or requested_end < requested_start:
            raise ValueError("Invalid source line range")

        actual_start = max(1, requested_start - context_lines)
        actual_end = min(len(lines), requested_end + context_lines)

        content = "\n".join(
            f"{number:>5}: {lines[number - 1]}"
            for number in range(actual_start, actual_end + 1)
        )

        return CodeContextResult(
            file=file,
            line_start=actual_start,
            line_end=actual_end,
            content=content,
        )


def _normalise_relative_path(file: str) -> PurePosixPath:
    normalized = file.replace("\\", "/").strip()
    path = PurePosixPath(normalized)

    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("Source path must be a relative path inside the target root")

    return path
