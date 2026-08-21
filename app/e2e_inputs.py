from __future__ import annotations

from pathlib import Path

from architecture.service import ArchitectureService
from sast.repository import JsonFindingRepository
from schemas.state import AgentState
from schemas.target import TargetProfile
from tools.runtime import LocalCodeReader


def build_real_initial_state(
    *,
    findings_path: str | Path,
    target_root: str | Path,
    architecture_path: str | Path | None = None,
    target_profile: TargetProfile | None = None,
    architecture_overrides_path: str | Path | None = None,
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

    service = target_profile.resolve_service(finding.file) if target_profile else None
    service = service or finding.service
    if not service:
        raise ValueError(
            f"Finding {finding.id} has no service, architecture context cannot be resolved"
        )
    if finding.service != service:
        finding = finding.model_copy(update={"service": service})

    effective_architecture = architecture_path
    if effective_architecture is None and target_profile is not None:
        effective_architecture = target_profile.architecture.file
    if effective_architecture is None:
        raise ValueError("Architecture context file is required")

    code = LocalCodeReader(target_root).read_code(
        finding.file,
        finding.line_start,
        finding.line_end,
    )
    architecture = ArchitectureService(
        effective_architecture,
        overrides_path=architecture_overrides_path,
    ).get_context(service)

    state: AgentState = {
        "finding": finding,
        "code_context": code.content,
        "architecture_context": architecture,
        "evidence": [],
        "iteration_count": 0,
        "max_iterations": max_iterations,
    }
    if target_profile is not None:
        state["target_profile"] = target_profile
    return state
