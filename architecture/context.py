from __future__ import annotations

from typing import Protocol

from schemas.architecture import (
    ArchitectureContext,
    ArchitectureOverrides,
    ProjectDescription,
)


class ArchitectureContextProvider(Protocol):
    def get_context(self, service: str) -> ArchitectureContext: ...


class ProjectDescriptionAdapter:
    """Build universal scoring facts from an in-memory project description."""

    def __init__(
        self,
        description: ProjectDescription,
        overrides: ArchitectureOverrides | None = None,
    ) -> None:
        self.description = description
        self.overrides = overrides or ArchitectureOverrides()

    def get_context(self, service: str) -> ArchitectureContext:
        services = self.description.services
        node = services.get(service)

        if node is None:
            context = ArchitectureContext(service=service)
        else:
            connected = list(dict.fromkeys(node.connects_to))
            databases = [
                name
                for name in connected
                if services.get(name) is not None
                and services[name].type.strip().lower() == "database"
            ]
            critical_paths = [
                f"{service} -> {name}"
                for name in connected
                if services.get(name) is not None
                and services[name].criticality.strip().lower() in {"high", "critical"}
            ]
            context = ArchitectureContext(
                service=service,
                public_exposure=node.public,
                criticality=node.criticality,
                trust_zone=node.trust_zone,
                connected_services=connected,
                databases=databases,
                critical_paths=critical_paths,
                authentication=node.authentication,
                blast_radius=node.blast_radius,
            )

        override = self.overrides.services.get(service)
        if override is None:
            return context

        values = override.model_dump(exclude_unset=True, exclude_none=True)
        return context.model_copy(update=values)
