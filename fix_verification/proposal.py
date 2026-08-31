from __future__ import annotations

from typing import Protocol

from schemas.fix import ProposedPatch
from schemas.report import FinalReport


class StructuredPatchClient(Protocol):
    def complete_model(
        self,
        *,
        output_model: type[ProposedPatch],
        system_prompt: str,
        user_payload: dict,
        operation: str,
    ) -> ProposedPatch: ...


class PatchProposalService:
    """Просим у LLM только diff artifact, доступ к выполнению она не получает"""

    def __init__(self, client: StructuredPatchClient) -> None:
        self.client = client

    def propose(self, report: FinalReport) -> ProposedPatch:
        if report.status != "confirmed":
            raise ValueError("A patch may be proposed only for a CONFIRMED finding")

        proposal = self.client.complete_model(
            output_model=ProposedPatch,
            system_prompt=(
                "You produce the smallest defensive unified diff for a confirmed finding. "
                "Return a patch artifact only. Never propose commands, commits, pushes, "
                "unrelated refactors, or changes outside the supplied code context."
            ),
            user_payload={
                "task": "Propose a minimal patch for this confirmed finding.",
                "finding": report.finding.model_dump(mode="json"),
                "code_context": report.code_context,
                "architecture_context": (
                    report.architecture_context.model_dump(mode="json")
                    if report.architecture_context is not None
                    else None
                ),
                "runtime_evidence": [
                    item.model_dump(mode="json") for item in report.evidence
                ],
                "limitations": report.limitations,
            },
            operation="propose_patch",
        )
        if proposal.finding_id != report.finding_id:
            raise ValueError("Patch proposal finding_id does not match the report")
        if not proposal.unified_diff.strip():
            raise ValueError("Patch proposal did not contain a unified diff")
        return proposal
