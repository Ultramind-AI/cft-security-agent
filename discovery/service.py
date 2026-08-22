from __future__ import annotations

from pathlib import Path

from discovery.base import DiscoveryContext, ProjectDetector
from discovery.detectors import default_detectors
from discovery.profile import TargetProfileBuilder
from discovery.resolver import DiscoveryResolver
from schemas.discovery import ProjectDiscoveryResult
from schemas.target import TargetProfile


class ProjectDiscovery:
    def __init__(
        self,
        detectors: tuple[ProjectDetector, ...] | None = None,
        resolver: DiscoveryResolver | None = None,
    ) -> None:
        self.detectors = detectors or default_detectors()
        self.resolver = resolver or DiscoveryResolver()

    def discover(self, repository_root: str | Path) -> ProjectDiscoveryResult:
        context = DiscoveryContext.scan(repository_root)
        signals = []
        for detector in self.detectors:
            signals.extend(detector.detect(context))
        return self.resolver.resolve(context.root, signals)

    def build_profile(
        self,
        discovery: ProjectDiscoveryResult,
        *,
        profile_id: str = "discovered",
        name: str = "",
        architecture_file: str | Path | None = None,
        base_profile: TargetProfile | None = None,
    ) -> TargetProfile:
        return TargetProfileBuilder().build(
            discovery,
            profile_id=profile_id,
            name=name,
            architecture_file=architecture_file,
            base_profile=base_profile,
        )
