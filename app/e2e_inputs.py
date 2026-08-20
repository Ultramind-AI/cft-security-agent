from __future__ import annotations

from pathlib import Path

from architecture.service import ArchitectureService
from sast.repository import JsonFindingRepository
from schemas.state import AgentState
from tools.runtime import LocalCodeReader


def build_real_initial_state(
    *,
    findings_path: str | Path,
    target_root: str | Path,
    architecture_path: str | Path,
    finding_id: str | None = None,
    finding_index: int = 0,
    max_iterations: int = 1,
) -> AgentState:
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    findings = JsonFindingRepository(findings_path)
    finding = (
        findings.get_finding(finding_id)
        if finding_id is not None
        else findings.get_by_index(finding_index)
    )

    if not finding.service:
        raise ValueError(
            f"Finding {finding.id} has no service, architecture context cannot be resolved"
        )

    # Состояние собираем из реального таргета, чтобы агент не дорисовывал контекст
    code = LocalCodeReader(target_root).read_code(
        finding.file,
        finding.line_start,
        finding.line_end,
    )
    architecture = ArchitectureService(architecture_path).get_context(finding.service)

    return {
        "finding": finding,
        "code_context": code.content,
        "architecture_context": architecture,
        "evidence": [],
        "iteration_count": 0,
        "max_iterations": max_iterations,
    }
