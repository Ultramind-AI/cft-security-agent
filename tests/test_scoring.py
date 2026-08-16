from architecture.service import ArchitectureService
from schemas.architecture import ArchitectureContext
from schemas.finding import Finding
from scoring.service import ScoringService


def _finding(*, rule_id: str = "test.rule") -> Finding:
    return Finding(
        id="finding-1",
        source="semgrep",
        rule_id=rule_id,
        title="Test finding",
        description="Controlled scoring test.",
        file="backend/Dockerfile",
        line_start=14,
        severity="ERROR",
        service="backend",
    )


def test_docker_missing_user_is_cvss_not_applicable() -> None:
    result = ScoringService().score_cvss(
        _finding(rule_id="dockerfile.security.missing-user.missing-user")
    )

    assert result.vector == "N/A"
    assert result.score is None
    assert result.severity == "N/A"
    assert "false precision" in result.reasoning


def test_semgrep_severity_is_not_guessed_into_cvss() -> None:
    result = ScoringService().score_cvss(_finding(rule_id="python.security.example"))

    assert result.vector == "UNASSESSED"
    assert result.score is None
    assert result.severity == "UNASSESSED"
    assert "Semgrep severity is not converted into CVSS" in result.reasoning


def test_sberlab_backend_context_priority_is_real_and_deterministic() -> None:
    context = ArchitectureService("targets/sberlab_architecture.yaml").get_context("backend")
    result = ScoringService().score_context_priority(context)

    assert context.public_exposure is True
    assert context.databases == ["database"]
    assert "backend -> database" in context.critical_paths
    assert result.score == 7.0
    assert result.level == "HIGH"
    assert "public_exposure:internet:+2" in result.reasons
    assert "asset_criticality:high:+2" in result.reasons
    assert "database_access:direct:+2" in result.reasons
    assert "critical_path:direct:+1" in result.reasons
    assert "authentication:unknown:+0" in result.reasons
    assert "blast_radius:unknown:+0" in result.reasons


def test_context_priority_thresholds() -> None:
    scorer = ScoringService()

    low = scorer.score_context_priority(ArchitectureContext(service="test"))
    medium = scorer.score_context_priority(
        ArchitectureContext(
            service="frontend",
            public_exposure=True,
            criticality="medium",
            authentication="user",
        )
    )
    high = scorer.score_context_priority(
        ArchitectureContext(
            service="backend",
            public_exposure=True,
            criticality="high",
            databases=["database"],
            critical_paths=["backend -> database"],
        )
    )

    assert (low.score, low.level) == (0.0, "LOW")
    assert (medium.score, medium.level) == (3.5, "MEDIUM")
    assert (high.score, high.level) == (7.0, "HIGH")
