from __future__ import annotations

import re
from pathlib import Path

from schemas.discovery import DiscoveredComponent, ProjectDiscoveryResult
from schemas.target import (
    TargetArchitectureConfig,
    TargetArtifact,
    TargetConstraints,
    TargetHealthcheck,
    TargetProfile,
    TargetRuntimeConfig,
    TargetService,
)

_ARTIFACT_ID = re.compile(r"[^a-z0-9_-]+")


class TargetProfileBuilder:
    def build(
        self,
        discovery: ProjectDiscoveryResult,
        *,
        profile_id: str = "discovered",
        name: str = "",
        architecture_file: str | Path | None = None,
        base_profile: TargetProfile | None = None,
    ) -> TargetProfile:
        existing_services = base_profile.services.values() if base_profile else []
        existing_by_root = {service.root: service for service in existing_services}
        services: dict[str, TargetService] = {}
        matched_existing_roots: set[str] = set()

        for component in discovery.components:
            detected = _service_from_component(component)
            existing = existing_by_root.get(component.root)
            if existing is not None:
                service = _merge_service(detected, existing)
                matched_existing_roots.add(component.root)
            else:
                service = detected
            services[service.id] = service

        if base_profile is not None:
            for service in base_profile.services.values():
                if service.root not in matched_existing_roots:
                    services[service.id] = service

        artifacts = dict(base_profile.artifacts) if base_profile is not None else {}
        known_paths = {artifact.relative_path for artifact in artifacts.values()}
        for component in discovery.components:
            for path, kind in [
                *((path, "dockerfile") for path in component.dockerfiles),
                *((path, "dependency_manifest") for path in component.dependency_files),
            ]:
                if path in known_paths:
                    continue
                artifact_id = _unique_artifact_id(component.id, path, kind, artifacts)
                artifacts[artifact_id] = TargetArtifact(id=artifact_id, kind=kind, path=path)
                known_paths.add(path)

        if base_profile is not None:
            architecture = base_profile.architecture
            runtime = base_profile.runtime
            constraints = base_profile.constraints
            sast = base_profile.sast
            metadata = dict(base_profile.metadata)
            environment = base_profile.environment
            effective_id = base_profile.id
            effective_name = base_profile.name
        else:
            architecture = TargetArchitectureConfig(
                file=Path(architecture_file) if architecture_file is not None else None
            )
            runtime = TargetRuntimeConfig(type=_runtime_type(discovery))
            constraints = TargetConstraints()
            sast = None
            metadata = {}
            environment = "local"
            effective_id = profile_id
            effective_name = name

        metadata["discovery.version"] = "1"
        payload = {
            "id": effective_id,
            "name": effective_name,
            "repository_path": discovery.repository_root,
            "environment": environment,
            "architecture": architecture,
            "runtime": runtime,
            "services": services,
            "artifacts": artifacts,
            "constraints": constraints,
            "metadata": metadata,
        }
        if sast is not None:
            payload["sast"] = sast
        return TargetProfile.model_validate(payload)


def _service_from_component(component: DiscoveredComponent) -> TargetService:
    compose = component.compose_candidates[0] if component.compose_candidates else None
    healthcheck = (
        TargetHealthcheck(path=component.healthcheck_candidates[0].path)
        if component.healthcheck_candidates
        else None
    )
    return TargetService(
        id=component.id,
        type=_component_type(component),
        root=component.root,
        dependency_files=component.dependency_files,
        dockerfile=component.dockerfiles[0] if component.dockerfiles else None,
        compose_file=compose.compose_file if compose else None,
        compose_service=compose.service if compose else None,
        build=component.build_candidates[0].command if component.build_candidates else [],
        run=component.run_candidates[0].command if component.run_candidates else [],
        healthcheck=healthcheck,
        allowed_local_addresses=component.allowed_local_addresses,
    )


def _merge_service(detected: TargetService, existing: TargetService) -> TargetService:
    # Ручной профиль имеет приоритет над discovery
    return detected.model_copy(
        update={
            "id": existing.id,
            "type": existing.type if existing.type != "unknown" else detected.type,
            "dependency_files": existing.dependency_files or detected.dependency_files,
            "dockerfile": existing.dockerfile or detected.dockerfile,
            "compose_file": existing.compose_file or detected.compose_file,
            "compose_service": existing.compose_service or detected.compose_service,
            "build": existing.build or detected.build,
            "run": existing.run or detected.run,
            "healthcheck": existing.healthcheck or detected.healthcheck,
            "allowed_local_addresses": (
                existing.allowed_local_addresses or detected.allowed_local_addresses
            ),
        }
    )


def _component_type(component: DiscoveredComponent) -> str:
    descriptors = component.frameworks or component.technologies
    return "+".join(descriptors) if descriptors else "unknown"


def _runtime_type(discovery: ProjectDiscoveryResult) -> str:
    if any(component.compose_candidates for component in discovery.components):
        return "docker_compose"
    if any(component.dockerfiles for component in discovery.components):
        return "dockerfile"
    return "unknown"


def _unique_artifact_id(
    component_id: str,
    path: str,
    kind: str,
    artifacts: dict[str, TargetArtifact],
) -> str:
    basename = Path(path).name.lower()
    base = _ARTIFACT_ID.sub("-", f"{component_id}-{kind}-{basename}").strip("-_")
    candidate = base or f"{component_id}-{kind}"
    suffix = 2
    while candidate in artifacts:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate
