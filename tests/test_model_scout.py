from app.config import settings
from app.pipeline_run import _append_scout_findings
from discovery.scout import validate_scout_candidates
from schemas.discovery import ProjectDiscoveryResult
from schemas.finding import Finding
from schemas.scout import CandidateFinding, CandidateFindingBatch


def test_scout_candidate_needs_inventory_provenance() -> None:
    discovery = ProjectDiscoveryResult(
        repository_root=".",
        project_files=["app/main.py"],
    )
    candidate = CandidateFinding(
        rule_id="scout.test",
        title="Candidate",
        description="Needs verification",
        file="app/main.py",
        provenance_paths=["app/main.py"],
        rationale="Observed source anchor",
    )

    accepted = validate_scout_candidates([candidate], discovery)

    assert accepted[0].to_finding(finding_id="scout-1").source == "model_scout"


def test_scout_candidate_with_unknown_provenance_is_dropped() -> None:
    discovery = ProjectDiscoveryResult(repository_root=".", project_files=["app/main.py"])
    candidate = CandidateFinding(
        rule_id="scout.test",
        title="Candidate",
        description="Needs verification",
        file="app/main.py",
        provenance_paths=["unknown.py"],
        rationale="Not trusted",
    )

    assert validate_scout_candidates([candidate], discovery) == []


def test_scout_candidate_enters_normal_finding_list(monkeypatch) -> None:
    discovery = ProjectDiscoveryResult(repository_root=".", project_files=["app/main.py"])
    candidate = CandidateFinding(
        rule_id="scout.test",
        title="Candidate",
        description="Needs verification",
        file="app/main.py",
        provenance_paths=["app/main.py"],
        rationale="Observed source anchor",
    )

    class _Scout:
        def __init__(self, _client) -> None:
            pass

        def scout(self, _inventory):
            return CandidateFindingBatch(candidates=[candidate])

    class _Model:
        client = object()

        @classmethod
        def from_settings(cls, _settings):
            return cls()

    monkeypatch.setattr("app.pipeline_run.RepositoryScout", _Scout)
    monkeypatch.setattr("app.pipeline_run.FallbackLLMAgentModel", _Model)
    monkeypatch.setattr(settings, "agent_mode", "llm")
    initial = [
        Finding(
            id="sast-1",
            source="semgrep",
            rule_id="existing",
            title="Existing",
            description="Existing",
            file="app/main.py",
        )
    ]

    result = _append_scout_findings(initial, discovery)

    assert [item.source for item in result] == ["semgrep", "model_scout"]
