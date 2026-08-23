"""Хранение timeline и явное преобразование его событий в Evidence."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from schemas.action import ActionProposal
from schemas.evidence import (
    Evidence,
    EvidenceAction,
    EvidenceArtifact,
    EvidenceObservation,
    EvidenceReliability,
    EvidenceScope,
)
from schemas.runtime_telemetry import RuntimeTelemetryEvent, RuntimeTelemetryTimeline


class JsonRuntimeTelemetryStore:
    """Хранит timeline вне одноразовой рабочей директории sandbox."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).resolve()

    def put(self, timeline: RuntimeTelemetryTimeline) -> tuple[str, str]:
        self.directory.mkdir(parents=True, exist_ok=True)
        artifact_ref = f"telemetry-{uuid4().hex}"
        destination = self.directory / f"{artifact_ref}.json"
        temporary = destination.with_suffix(".tmp")
        payload = timeline.model_dump(mode="json")

        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(
                descriptor,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8"),
            )
        finally:
            os.close(descriptor)
        temporary.replace(destination)
        return artifact_ref, destination.as_posix()

    def get(self, artifact_ref: str) -> RuntimeTelemetryTimeline:
        if re.fullmatch(r"telemetry-[0-9a-f]{32}", artifact_ref) is None:
            raise ValueError("Invalid telemetry artifact reference")
        payload = json.loads(
            (self.directory / f"{artifact_ref}.json").read_text(encoding="utf-8")
        )
        return RuntimeTelemetryTimeline.model_validate(payload)


def build_telemetry_evidence(
    *,
    event: RuntimeTelemetryEvent,
    action: ActionProposal,
    hypothesis_id: str,
    artifact_ref: str,
    reliability: EvidenceReliability = "high",
) -> Evidence:
    """Связать фактическое событие с гипотезой без вывода от LLM."""

    if event.target != action.target:
        raise ValueError("Telemetry event target must match ActionProposal target")
    if event.service and action.service and event.service != action.service:
        raise ValueError("Telemetry event service must match ActionProposal service")

    observation_kind = f"runtime_{event.kind}"
    facts = {"telemetry_event_id": event.id, **event.facts}
    return Evidence(
        id=f"evidence-{uuid4().hex}",
        action_id=action.id,
        type=observation_kind,
        summary=f"Recorded target telemetry event: {event.kind}",
        artifact_refs=[artifact_ref],
        reliability=reliability,
        source="runtime",
        sandbox_session_id=event.session_id,
        hypothesis_id=hypothesis_id,
        action=EvidenceAction(id=action.id, tool=action.tool, run_id=event.run_id),
        observation=EvidenceObservation(
            kind=observation_kind,
            facts=facts,
            captured_at=event.observed_at,
        ),
        scope=EvidenceScope(
            target=action.target,
            environment=action.environment,
            service=event.service or action.service,
            description=f"Target telemetry event {event.id}",
        ),
        artifacts=[EvidenceArtifact(ref=artifact_ref, role="log")],
        created_at=event.observed_at,
    )
