"""Нормализуем model scout до того, как кандидат увидит основной pipeline."""

from __future__ import annotations

from collections.abc import Iterable

from schemas.discovery import ProjectDiscoveryResult
from schemas.scout import CandidateFinding


def validate_scout_candidates(
    candidates: Iterable[CandidateFinding],
    discovery: ProjectDiscoveryResult,
) -> list[CandidateFinding]:
    """Scout не может сослаться на файл вне trusted discovery inventory."""
    known_files = set(discovery.project_files)
    valid: list[CandidateFinding] = []
    for candidate in candidates:
        paths = set(candidate.provenance_paths)
        if candidate.file not in known_files or not paths.issubset(known_files):
            continue
        valid.append(candidate)
    return valid
