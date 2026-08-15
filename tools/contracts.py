from typing import Protocol

from schemas.action import ActionProposal
from schemas.architecture import ArchitectureContext
from schemas.evidence import Evidence
from schemas.finding import Finding
from schemas.scoring import CVSSResult, ContextPriority


class FindingReader(Protocol):
    def get_finding(self, finding_id: str) -> Finding: ...


class CodeReader(Protocol):
    def read_code(self, file: str, line_start: int | None, line_end: int | None) -> str: ...


class ArchitectureReader(Protocol):
    def get_context(self, service: str) -> ArchitectureContext: ...


class ScoringTool(Protocol):
    def calculate_cvss(self, finding: Finding) -> CVSSResult: ...

    def calculate_context_priority(
        self,
        finding: Finding,
        context: ArchitectureContext,
    ) -> ContextPriority: ...


class EvidenceReader(Protocol):
    def get_evidence(self, evidence_id: str) -> Evidence: ...


class ActionRequester(Protocol):
    def propose(self, action: ActionProposal) -> ActionProposal: ...
