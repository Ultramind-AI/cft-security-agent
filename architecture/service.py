from pathlib import Path

import yaml

from schemas.architecture import ArchitectureContext


class ArchitectureService:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    def get_context(self, service: str) -> ArchitectureContext:
        services = self.data.get("services", {})
        node = services.get(service, {})

        # Связи и критические пути берем только из явного connects_to графа
        connected = list(node.get("connects_to", []))

        databases = [
            name
            for name in connected
            if services.get(name, {}).get("type") == "database"
        ]

        critical_paths = [
            f"{service} -> {name}"
            for name in connected
            if str(services.get(name, {}).get("criticality", "unknown")).lower()
            in {"high", "critical"}
        ]

        return ArchitectureContext(
            service=service,
            public_exposure=bool(node.get("public", False)),
            criticality=str(node.get("criticality", "unknown")),
            trust_zone=node.get("trust_zone"),
            connected_services=connected,
            databases=databases,
            critical_paths=critical_paths,
            authentication=str(node.get("authentication", "unknown")),
            blast_radius=str(node.get("blast_radius", "unknown")),
        )
