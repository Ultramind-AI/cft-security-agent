import pytest

from architecture.context import ProjectDescriptionAdapter
from pr_analysis.git_diff import GitDiff, parse_git_diff
from pr_analysis.service import PRAnalysisService, finding_fingerprint
from schemas.architecture import ProjectDescription
from schemas.finding import Finding


def _finding(
    finding_id: str,
    *,
    rule: str,
    file: str,
    line: int,
    service: str = "api",
) -> Finding:
    return Finding(
        id=finding_id,
        source="semgrep",
        rule_id=rule,
        title="Stable message",
        description="Stable message",
        file=file,
        line_start=line,
        line_end=line,
        severity="ERROR",
        service=service,
    )


def _architecture(*, public: bool) -> ProjectDescriptionAdapter:
    description = ProjectDescription.model_validate(
        {
            "services": {
                "api": {
                    "type": "service",
                    "public": public,
                    "criticality": "high",
                }
            }
        }
    )
    return ProjectDescriptionAdapter(description)


def test_fingerprint_survives_line_movement_and_path_separators() -> None:
    base = _finding("base", rule="rule.x", file="src\\app.py", line=10)
    head = _finding("head", rule="rule.x", file="src/app.py", line=47)

    assert finding_fingerprint(base) == finding_fingerprint(head)


def test_dot_prefixed_paths_are_preserved_but_traversal_is_rejected() -> None:
    finding = _finding("dotfile", rule="rule.dot", file=".github/workflows/ci.yml", line=4)
    assert finding_fingerprint(finding) == finding_fingerprint(
        finding.model_copy(update={"file": "./.github/workflows/ci.yml"})
    )

    with pytest.raises(ValueError, match="inside the repository"):
        finding_fingerprint(finding.model_copy(update={"file": "../secrets.txt"}))

    with pytest.raises(ValueError, match="inside the repository"):
        finding_fingerprint(finding.model_copy(update={"file": "/etc/passwd"}))


def test_zero_context_diff_maps_head_lines_and_deleted_files() -> None:
    diff = parse_git_diff(
        """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -10 +10,2 @@
-old
+new
+added
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-gone
"""
    )

    assert diff.changed_files == ["src/app.py", "old.py"]
    assert diff.changed_lines == {"src/app.py": [10, 11], "old.py": []}


def test_pr_analysis_classifies_new_existing_and_affected_findings() -> None:
    base_findings = [
        _finding("base-affected", rule="rule.a", file="src/app.py", line=10),
        _finding(
            "base-existing",
            rule="rule.b",
            file="src/other.py",
            line=5,
            service="worker",
        ),
        _finding("base-architecture", rule="rule.c", file="src/config.py", line=3),
    ]
    head_findings = [
        _finding("affected", rule="rule.a", file="src/app.py", line=20),
        _finding(
            "existing",
            rule="rule.b",
            file="src/other.py",
            line=5,
            service="worker",
        ),
        _finding("architecture", rule="rule.c", file="src/config.py", line=3),
        _finding("new", rule="rule.new", file="src/new.py", line=7),
    ]
    analyser = PRAnalysisService(
        base_ref="main",
        head_ref="feature",
        diff=GitDiff(
            changed_files=["src/app.py", "src/new.py"],
            changed_lines={"src/app.py": [20], "src/new.py": [7]},
        ),
        base_architecture=_architecture(public=False),
        head_architecture=_architecture(public=True),
    )

    findings, summary = analyser.analyse(
        base_findings=base_findings,
        head_findings=head_findings,
    )
    classifications = {
        item.id: item.pr_context.classification for item in findings if item.pr_context
    }

    assert classifications == {
        "affected": "affected-by-change",
        "existing": "existing",
        "architecture": "affected-by-change",
        "new": "new",
    }
    assert summary.findings["affected"].changed_lines == [20]
    assert summary.findings["architecture"].architecture_context_changed is True
