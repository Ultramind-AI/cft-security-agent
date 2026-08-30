import pytest

from discovery.service import ProjectDiscovery
from schemas.discovery import (
    DiscoveryClaim,
    DiscoveryInterpretation,
    DiscoverySignal,
    ProjectDiscoveryResult,
)


class _Interpreter:
    def __init__(self, interpretation: DiscoveryInterpretation) -> None:
        self.interpretation = interpretation

    def interpret(self, _inventory: ProjectDiscoveryResult) -> DiscoveryInterpretation:
        return self.interpretation


def _inventory() -> ProjectDiscoveryResult:
    return ProjectDiscoveryResult(
        repository_root=".",
        project_files=["backend/pyproject.toml"],
        signals=[
            DiscoverySignal(
                detector="python",
                kind="technology",
                path="backend/pyproject.toml",
                value="python",
                confidence=1.0,
            )
        ],
    )


def test_interpretation_keeps_only_inventory_provenance() -> None:
    result = ProjectDiscovery().interpret(
        _inventory(),
        _Interpreter(
            DiscoveryInterpretation(
                summary="Python component",
                claims=[
                    DiscoveryClaim(
                        claim="Repository contains Python code",
                        source_paths=["backend/pyproject.toml"],
                        signal_values=["python"],
                    )
                ],
            )
        ),
    )

    assert result.interpretation is not None


def test_interpretation_rejects_hallucinated_file() -> None:
    with pytest.raises(ValueError, match="outside inventory"):
        ProjectDiscovery().interpret(
            _inventory(),
            _Interpreter(
                DiscoveryInterpretation(
                    summary="Bad claim",
                    claims=[
                        DiscoveryClaim(
                            claim="invented",
                            source_paths=["secret.txt"],
                        )
                    ],
                )
            ),
        )
