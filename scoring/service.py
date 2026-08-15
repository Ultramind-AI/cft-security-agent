from schemas.architecture import ArchitectureContext
from schemas.finding import Finding
from schemas.scoring import CVSSResult, ContextPriority

class ScoringService:
    """Starter scoring boundary. Replace CVSS stub with deterministic implementation."""
    def score(self, finding: Finding, context: ArchitectureContext) -> tuple[CVSSResult, ContextPriority]:
        cvss = CVSSResult(
            vector="CVSS:4.0/PLACEHOLDER",
            score=0.0,
            severity="UNASSESSED",
            reasoning="Starter stub. Replace with deterministic CVSS implementation.",
        )
        reasons: list[str] = []
        if context.public_exposure:
            reasons.append("public_exposure")
        if context.criticality.lower() in {"high", "critical"}:
            reasons.append(f"criticality:{context.criticality.lower()}")
        if context.databases:
            reasons.append("database_connectivity")
        return cvss, ContextPriority(level="UNASSESSED", score=None, reasons=reasons)
