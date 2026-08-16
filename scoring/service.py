from schemas.architecture import ArchitectureContext
from schemas.finding import Finding
from schemas.scoring import ContextPriority, CVSSResult

_NON_CVSS_RULE_PREFIXES = (
    "dockerfile.security.missing-user",
)

_CRITICALITY_POINTS = {
    "critical": 2.0,
    "high": 2.0,
    "medium": 1.0,
    "normal": 1.0,
    "low": 0.0,
    "test": 0.0,
    "dev": 0.0,
    "development": 0.0,
    "unknown": 0.0,
}

_AUTHENTICATION_POINTS = {
    "none": 1.0,
    "unauthenticated": 1.0,
    "public": 1.0,
    "user": 0.5,
    "authenticated": 0.5,
    "admin": 0.0,
    "administrator": 0.0,
    "unknown": 0.0,
}

_BLAST_RADIUS_POINTS = {
    "many": 2.0,
    "shared": 2.0,
    "shared_infrastructure": 2.0,
    "several": 1.0,
    "limited": 1.0,
    "isolated": 0.0,
    "unknown": 0.0,
}


class ScoringService:
    def score(
        self,
        finding: Finding,
        context: ArchitectureContext,
    ) -> tuple[CVSSResult, ContextPriority]:
        return self.score_cvss(finding), self.score_context_priority(context)

    def score_cvss(self, finding: Finding) -> CVSSResult:
        """Return an evidence-safe CVSS state without inventing missing metrics."""
        rule_id = finding.rule_id.lower()

        if rule_id.startswith(_NON_CVSS_RULE_PREFIXES):
            return CVSSResult(
                vector="N/A",
                score=None,
                severity="N/A",
                reasoning=(
                    "This SAST finding is a container hardening/configuration issue. "
                    "Without a demonstrated vulnerability and justified CVSS 4.0 metrics, "
                    "a numeric base score would create false precision."
                ),
            )

        return CVSSResult(
            vector="UNASSESSED",
            score=None,
            severity="UNASSESSED",
            reasoning=(
                "CVSS 4.0 may apply, but explicit justified metric values are not present "
                "in the current workflow input. Semgrep severity is not converted into CVSS."
            ),
        )

    def score_context_priority(
        self,
        context: ArchitectureContext,
    ) -> ContextPriority:
        """Calculate Context Priority v0.1 from explicit architecture facts."""
        score = 0.0
        reasons: list[str] = []

        exposure_points = 2.0 if context.public_exposure else 0.0
        score += exposure_points
        reasons.append(
            f"public_exposure:{'internet' if context.public_exposure else 'not_public'}:"
            f"+{_format_points(exposure_points)}"
        )

        criticality = context.criticality.strip().lower() or "unknown"
        criticality_points = _CRITICALITY_POINTS.get(criticality, 0.0)
        score += criticality_points
        reasons.append(
            f"asset_criticality:{criticality}:+{_format_points(criticality_points)}"
        )

        database_points = 2.0 if context.databases else 0.0
        score += database_points
        reasons.append(
            f"database_access:{'direct' if context.databases else 'none'}:"
            f"+{_format_points(database_points)}"
        )

        critical_path_points = 1.0 if context.critical_paths else 0.0
        score += critical_path_points
        reasons.append(
            f"critical_path:{'direct' if context.critical_paths else 'none'}:"
            f"+{_format_points(critical_path_points)}"
        )

        authentication = context.authentication.strip().lower() or "unknown"
        authentication_points = _AUTHENTICATION_POINTS.get(authentication, 0.0)
        score += authentication_points
        reasons.append(
            f"authentication:{authentication}:+{_format_points(authentication_points)}"
        )

        blast_radius = context.blast_radius.strip().lower() or "unknown"
        blast_radius_points = _BLAST_RADIUS_POINTS.get(blast_radius, 0.0)
        score += blast_radius_points
        reasons.append(
            f"blast_radius:{blast_radius}:+{_format_points(blast_radius_points)}"
        )

        return ContextPriority(
            level=_priority_level(score),
            score=score,
            reasons=reasons,
        )


def _priority_level(score: float) -> str:
    if score <= 3.0:
        return "LOW"
    if score <= 6.0:
        return "MEDIUM"
    return "HIGH"


def _format_points(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
