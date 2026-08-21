from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

RuntimeBackendType = Literal["process", "docker"]
NetworkModeType = Literal["none", "internal_bridge"]


@dataclass(frozen=True)
class SandboxLimits:
    """Ограничения ресурсов применяются как к средам выполнения процессов, так и к средам выполнения контейнеров"""
    wall_time_seconds: float = 5.0
    cpu_time_seconds: int = 2
    memory_bytes: int = 256 * 1024 * 1024  # 256 MiB
    max_file_bytes: int = 1024 * 1024       # 1 MiB single file limit
    max_processes: int = 8                  # PIDs limit
    max_output_bytes: int = 16_384          # 16 KiB output stream cap
    tmpfs_size_bytes: int = 64 * 1024 * 1024 # 64 MiB total ephemeral quota
    umask: int = 0o077
    max_cpus: float = 1.0


@dataclass(frozen=True)
class SandboxPolicy:
    """Единая политика, определяющая границы выполнения и бэкенд безопасности."""

    backend: RuntimeBackendType = "docker"
    network_mode: NetworkModeType = "none"
    allowed_internal_network: str = "cft_internal_security_net"

    allowed_environments: set[str] = field(
        default_factory=lambda: {"local", "sandbox", "staging"}
    )

    limits: SandboxLimits = field(default_factory=SandboxLimits)

    sandbox_image: str = ""

    container_user: str = "65534:65534"
    fail_closed: bool = True

    def __post_init__(self) -> None:
        if self.backend not in {"docker", "process"}:
            raise ValueError(
                f"Unsupported sandbox backend: {self.backend!r}"
            )

        if self.network_mode not in {"none", "internal_bridge"}:
            raise ValueError(
                f"Unsupported network mode: {self.network_mode!r}"
            )

        if self.backend == "docker":
            if not self.sandbox_image:
                raise ValueError(
                    "Docker sandbox requires sandbox_image"
                )

            if "@sha256:" not in self.sandbox_image:
                raise ValueError(
                    "Docker sandbox image must be pinned by immutable "
                    "SHA-256 digest"
                )

    def validate_target_environment(self, environment: str) -> None:
        """Обеспечивает соблюдение списка разрешенных сред. Целевые среды уровня Production заблокированы."""
        if environment not in self.allowed_environments:
            raise ValueError(
                f"Target environment '{environment}' is blocked by "
                f"security policy. Allowed environments: "
                f"{sorted(self.allowed_environments)}"
            )

    def validate_for_production(
        self,
        is_docker_available: bool,
    ) -> None:
        if (
            self.backend == "docker"
            and self.fail_closed
            and not is_docker_available
        ):
            raise RuntimeError(
                "Security Boundary Failure: Docker runtime is required "
                "for secure sandbox execution, but Docker is unavailable. "
                "Refusing to fall back to ProcessSandbox."
            )
