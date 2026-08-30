"""LLM scout ищет гипотезы по inventory, не обходит SAST и Evidence pipeline."""

from __future__ import annotations

from agent.llm import ProviderFailoverClient
from agent.prompts import SYSTEM_PROMPT
from schemas.discovery import ProjectDiscoveryResult
from schemas.scout import CandidateFindingBatch


class RepositoryScout:
    def __init__(self, client: ProviderFailoverClient) -> None:
        self.client = client

    def scout(self, inventory: ProjectDiscoveryResult) -> CandidateFindingBatch:
        """Модель видит только inventory и обязана вернуть проверяемую provenance."""
        return self.client.complete_model(
            output_model=CandidateFindingBatch,
            system_prompt=SYSTEM_PROMPT,
            user_payload={
                "task": (
                    "Propose defensive CandidateFinding items from this deterministic "
                    "repository inventory. Every candidate must use source=model_scout, "
                    "file and provenance_paths from project_files. Candidates are not "
                    "confirmed and must not contain a verdict."
                ),
                "project_files": inventory.project_files,
                "signals": [signal.model_dump(mode="json") for signal in inventory.signals],
                "components": [
                    component.model_dump(mode="json") for component in inventory.components
                ],
            },
            operation="repository_scout",
        )
