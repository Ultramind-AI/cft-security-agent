from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath

from architecture.context import ArchitectureContextProvider
from pr_analysis.git_diff import GitDiff
from schemas.finding import Finding
from schemas.pr import PRAnalysisSummary, PRFindingContext


def finding_fingerprint(finding: Finding) -> str:
    """Строим стабильный id без номеров строк, они меняются между ревизиями"""
    identity = {
        "source": finding.source.strip().lower(),
        "rule_id": finding.rule_id.strip(),
        "file": _normalize_path(finding.file),
        "description": " ".join((finding.description or finding.title).split()),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class PRAnalysisService:
    def __init__(
        self,
        *,
        base_ref: str,
        head_ref: str,
        diff: GitDiff,
        base_architecture: ArchitectureContextProvider | None = None,
        head_architecture: ArchitectureContextProvider | None = None,
    ) -> None:
        self.base_ref = base_ref
        self.head_ref = head_ref
        self.diff = diff
        self.base_architecture = base_architecture
        self.head_architecture = head_architecture

    def analyse(
        self,
        *,
        base_findings: list[Finding],
        head_findings: list[Finding],
    ) -> tuple[list[Finding], PRAnalysisSummary]:
        base_fingerprints = {finding_fingerprint(item) for item in base_findings}
        enriched: list[Finding] = []
        contexts: dict[str, PRFindingContext] = {}

        for finding in head_findings:
            fingerprint = finding_fingerprint(finding)
            path = _normalize_path(finding.file)
            file_lines = self.diff.changed_lines.get(path, [])
            finding_lines = _finding_lines(finding)
            relevant_lines = sorted(set(file_lines).intersection(finding_lines))
            architecture_changed = self._architecture_changed(finding.service)

            if fingerprint not in base_fingerprints:
                classification = "new"
            elif relevant_lines or architecture_changed:
                classification = "affected-by-change"
            else:
                classification = "existing"

            context = PRFindingContext(
                fingerprint=fingerprint,
                classification=classification,
                base_ref=self.base_ref,
                head_ref=self.head_ref,
                changed_file=path in self.diff.changed_files,
                changed_lines=relevant_lines,
                architecture_context_changed=architecture_changed,
            )
            enriched_finding = finding.model_copy(update={"pr_context": context})
            enriched.append(enriched_finding)
            contexts[finding.id] = context

        summary = PRAnalysisSummary(
            base_ref=self.base_ref,
            head_ref=self.head_ref,
            changed_files=self.diff.changed_files,
            changed_lines=self.diff.changed_lines,
            findings=contexts,
        )
        return enriched, summary

    def _architecture_changed(self, service: str | None) -> bool:
        if (
            service is None
            or self.base_architecture is None
            or self.head_architecture is None
        ):
            return False
        base = self.base_architecture.get_context(service)
        head = self.head_architecture.get_context(service)
        return base.model_dump() != head.model_dump()


def _normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("PR finding path must stay inside the repository")
    return path.as_posix()


def _finding_lines(finding: Finding) -> set[int]:
    if finding.line_start is None:
        return set()
    end = finding.line_end if finding.line_end is not None else finding.line_start
    return set(range(finding.line_start, end + 1))
