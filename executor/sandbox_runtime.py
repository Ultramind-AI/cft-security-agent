from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from executor.sandbox_policy import SandboxPolicy


@dataclass(frozen=True)
class ContainerRuntimeSpec:
    """Неизменяемые свойства команды CLI и выполнения для Docker Sandbox"""
    argv: list[str]
    container_name: str
    workspace_id: str
    network: str
    read_only_root: bool = True
    no_new_privileges: bool = True
    dropped_caps: str = "ALL"


class DockerRuntimeBuilder:
    """Формирует защищенные команды для контейнеров OCI/Docker в соответствии с моделью угроз."""

    def __init__(self, policy: SandboxPolicy) -> None:
        self.policy = policy

    def _workspace_tmpfs_options(self) -> str:
        """Владельца tmpfs берем из настроенного non-root Docker user"""
        # tmpfs получает UID/GID non-root пользователя, иначе /workspace недоступен для записи.
        user_parts = self.policy.container_user.split(":", maxsplit=1)
        if len(user_parts) != 2 or not all(part.isdecimal() for part in user_parts):
            raise ValueError(
                "Docker sandbox container_user must be a numeric UID:GID "
                "so the tmpfs workspace can be owned safely"
            )
        uid, gid = user_parts
        return (
            "/workspace:rw,noexec,nosuid,nodev,"
            f"size={self.policy.limits.tmpfs_size_bytes},uid={uid},gid={gid},mode=0700"
        )

    def build_spec(
            self,
            *,
            run_id: str,
            target_repo_host_path: Path | None,
            worker_script_host_path: Path,
            target_network_enabled: bool = False,
            network_name: str | None = None,
            detached: bool = False,
    ) -> ContainerRuntimeSpec:
        workspace_id = f"run-{run_id}"
        container_name = f"cft-sandbox-{run_id}"
        limits = self.policy.limits

        if network_name is not None:
            network_arg = network_name
        elif (
                target_network_enabled
                and self.policy.network_mode == "internal_bridge"
        ):
            network_arg = self.policy.allowed_internal_network
        else:
            network_arg = "none"

        worker_script_host_path = worker_script_host_path.resolve()

        if not worker_script_host_path.is_file():
            raise ValueError(
                "Sandbox worker script must be an existing file: "
                f"{worker_script_host_path}"
            )

        cmd: list[str] = [
            "docker",
            "run",
            "--rm" if not detached else "--detach",
            "-i",
            "--name",
            container_name,
            "--user",
            self.policy.container_user,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(limits.max_processes),
            "--memory",
            f"{limits.memory_bytes}b",
            "--cpus",
            str(limits.max_cpus),
            "--network",
            network_arg,
            "--tmpfs",
            self._workspace_tmpfs_options(),
            "-w",
            "/workspace",
        ]

        if target_repo_host_path is not None:
            target_repo_host_path = target_repo_host_path.resolve()

            if not target_repo_host_path.is_dir():
                raise ValueError(
                    "Trusted target repository path must be an existing "
                    f"directory: {target_repo_host_path}"
                )

            cmd.extend([
                "-v",
                f"{target_repo_host_path}:/target:ro",
            ])

        cmd.extend(["-v", f"{worker_script_host_path}:/app/worker.py:ro", self.policy.sandbox_image])
        if detached:
            cmd.extend(["python", "-c", "import time; time.sleep(3600)"])
        else:
            cmd.extend(["python", "/app/worker.py"])

        return ContainerRuntimeSpec(
            argv=cmd,
            container_name=container_name,
            workspace_id=workspace_id,
            network=network_arg,
            read_only_root=True,
            no_new_privileges=True,
            dropped_caps="ALL",
        )
