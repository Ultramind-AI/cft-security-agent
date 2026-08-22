from __future__ import annotations

import re
from pathlib import PurePosixPath

from schemas.discovery import (
    DiscoveredComponent,
    DiscoveryCommandCandidate,
    DiscoveryComposeCandidate,
    DiscoveryHealthcheckCandidate,
    DiscoverySignal,
    ProjectDiscoveryResult,
)

_COMPONENT_ID = re.compile(r"[^a-z0-9_-]+")


class DiscoveryResolver:
    def resolve(self, repository_root, signals: list[DiscoverySignal]) -> ProjectDiscoveryResult:
        ordered_signals = sorted(signals, key=_signal_sort_key)
        roots = self._component_roots(ordered_signals)
        used_ids: set[str] = set()
        components: list[DiscoveredComponent] = []
        warnings: list[str] = []

        for root in sorted(roots, key=_root_sort_key):
            root_signals = [signal for signal in ordered_signals if signal.component_root == root]
            component, component_warnings = self._build_component(root, root_signals, used_ids)
            components.append(component)
            warnings.extend(component_warnings)

        matched_roots = {component.root for component in components}
        project_files = sorted(
            {
                signal.path
                for signal in ordered_signals
                if signal.component_root is None or signal.component_root not in matched_roots
            }
        )
        return ProjectDiscoveryResult(
            repository_root=repository_root,
            components=components,
            signals=ordered_signals,
            project_files=project_files,
            warnings=warnings,
        )

    def _component_roots(self, signals: list[DiscoverySignal]) -> set[str]:
        anchored = {
            signal.component_root
            for signal in signals
            if signal.anchor and signal.component_root is not None
        }
        manifest_roots = {
            signal.component_root
            for signal in signals
            if signal.kind == "manifest" and signal.component_root is not None
        }

        roots = set(anchored)
        for root in manifest_roots:
            if not any(_is_descendant(anchor, root) for anchor in anchored if anchor != root):
                roots.add(root)
        return roots

    def _build_component(
        self,
        root: str,
        signals: list[DiscoverySignal],
        used_ids: set[str],
    ) -> tuple[DiscoveredComponent, list[str]]:
        technologies = sorted(
            {signal.value for signal in signals if signal.kind == "technology" and signal.value}
        )
        frameworks = sorted(
            {signal.value for signal in signals if signal.kind == "framework" and signal.value}
        )
        dependency_files = sorted(
            {signal.path for signal in signals if signal.kind == "manifest"}
        )
        dockerfiles = sorted(
            {signal.value or signal.path for signal in signals if signal.kind == "dockerfile"}
        )
        compose_candidates = sorted(
            [
                DiscoveryComposeCandidate(
                    compose_file=signal.metadata["compose_file"],
                    service=signal.value or "",
                    confidence=signal.confidence,
                )
                for signal in signals
                if signal.kind == "compose_service"
                and signal.value
                and signal.metadata.get("compose_file")
            ],
            key=lambda item: (-item.confidence, item.compose_file, item.service),
        )
        build_candidates = _command_candidates(signals, "build_command", "build")
        run_candidates = _command_candidates(signals, "run_command", "run")
        healthcheck_candidates = sorted(
            [
                DiscoveryHealthcheckCandidate(
                    path=signal.value or "",
                    source_path=signal.path,
                    confidence=signal.confidence,
                )
                for signal in signals
                if signal.kind == "healthcheck" and signal.value
            ],
            key=lambda item: (-item.confidence, item.source_path, item.path),
        )
        local_addresses = sorted(
            {signal.value for signal in signals if signal.kind == "local_address" and signal.value}
        )

        component_id = _component_id(root, compose_candidates, used_ids)
        used_ids.add(component_id)
        anchor_confidence = [signal.confidence for signal in signals if signal.anchor]
        confidence = max(anchor_confidence or [signal.confidence for signal in signals] or [0.0])
        warnings: list[str] = []
        if len(compose_candidates) > 1:
            warnings.append(
                f"Component '{component_id}' has multiple Compose services for root '{root}'; "
                f"profile selection will use '{compose_candidates[0].service}'"
            )
        if len(dockerfiles) > 1:
            warnings.append(
                f"Component '{component_id}' has multiple Dockerfiles; "
                "profile selection is deterministic"
            )

        return (
            DiscoveredComponent(
                id=component_id,
                root=root,
                technologies=technologies,
                frameworks=frameworks,
                dependency_files=dependency_files,
                dockerfiles=dockerfiles,
                compose_candidates=compose_candidates,
                build_candidates=build_candidates,
                run_candidates=run_candidates,
                healthcheck_candidates=healthcheck_candidates,
                allowed_local_addresses=local_addresses,
                source_paths=sorted({signal.path for signal in signals}),
                confidence=confidence,
            ),
            warnings,
        )


def _command_candidates(
    signals: list[DiscoverySignal], signal_kind: str, command_kind: str
) -> list[DiscoveryCommandCandidate]:
    candidates: dict[tuple[str, ...], DiscoveryCommandCandidate] = {}
    for signal in signals:
        if signal.kind != signal_kind or not signal.command:
            continue
        key = tuple(signal.command)
        candidate = DiscoveryCommandCandidate(
            kind=command_kind,
            command=signal.command,
            source_path=signal.path,
            confidence=signal.confidence,
        )
        current = candidates.get(key)
        if current is None or candidate.confidence > current.confidence:
            candidates[key] = candidate
    return sorted(
        candidates.values(),
        key=lambda item: (-item.confidence, item.source_path, tuple(item.command)),
    )


def _component_id(
    root: str,
    compose_candidates: list[DiscoveryComposeCandidate],
    used_ids: set[str],
) -> str:
    if compose_candidates:
        base = compose_candidates[0].service
    elif root == ".":
        base = "service"
    else:
        base = PurePosixPath(root).name
    base = _COMPONENT_ID.sub("-", base.strip().lower()).strip("-_") or "service"
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _is_descendant(candidate: str, parent: str) -> bool:
    if parent == ".":
        return candidate != "."
    return candidate.startswith(f"{parent}/")


def _root_sort_key(root: str) -> tuple[int, str]:
    return (len(PurePosixPath(root).parts) if root != "." else 0, root)


def _signal_sort_key(signal: DiscoverySignal) -> tuple[str, str, str, str, str]:
    return (
        signal.component_root or "",
        signal.path,
        signal.detector,
        signal.kind,
        signal.value or "",
    )
