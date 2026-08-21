from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from executor.sandbox import DockerSandbox, SandboxRequest
from executor.sandbox_policy import SandboxPolicy

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False

    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except OSError:
        return False

    return result.returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _docker_available() or not os.getenv("CFT_SANDBOX_IMAGE"),
        reason="Docker integration tests require a working Docker daemon and CFT_SANDBOX_IMAGE pinned by digest",
    ),
]

TEST_SANDBOX_IMAGE = os.environ.get("CFT_SANDBOX_IMAGE", "")

def test_docker_socket_is_not_available_inside_sandbox(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "check_socket.py"

    worker.write_text(
        """
import os
import socket
import sys

path = "/var/run/docker.sock"

if os.path.exists(path):
    print("docker socket path exists", file=sys.stderr)
    raise SystemExit(1)

print("socket-absent")
""",
        encoding="utf-8",
    )

    sandbox = DockerSandbox(
        policy=SandboxPolicy(
            backend="docker",
            network_mode="none",
            sandbox_image=TEST_SANDBOX_IMAGE,
        ),
        worker_path=worker,
    )

    result = sandbox.run(
        SandboxRequest(
            run_id="socket-test",
            tool="safe_noop",
            base_url="http://127.0.0.1",
            parameters={},
            request_timeout_seconds=2,
            network_access="none",
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "socket-absent"

def test_root_filesystem_is_read_only_inside_docker_sandbox(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "readonly_worker.py"

    worker.write_text(
        """
from pathlib import Path

try:
    Path("/rootfs-write-test").write_text("blocked")
except OSError:
    print("root-read-only")
else:
    raise SystemExit("root filesystem is writable")
""",
        encoding="utf-8",
    )

    sandbox = DockerSandbox(
        policy=SandboxPolicy(
            backend="docker",
            network_mode="none",
            sandbox_image=TEST_SANDBOX_IMAGE,
        ),
        worker_path=worker,
    )

    result = sandbox.run(
        SandboxRequest(
            run_id="readonly-root",
            tool="safe_noop",
            base_url="http://127.0.0.1",
            parameters={},
            request_timeout_seconds=2,
            network_access="none",
        )
    )

    assert result.exit_code == 0
    assert "root-read-only" in result.stdout
def test_ephemeral_workspace_is_writable_inside_docker_sandbox(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "workspace_worker.py"

    worker.write_text(
        """
from pathlib import Path

path = Path("/workspace/check.txt")
path.write_text("ok")
print(path.read_text())
""",
        encoding="utf-8",
    )

    sandbox = DockerSandbox(
        policy=SandboxPolicy(
            backend="docker",
            network_mode="none",
            sandbox_image=TEST_SANDBOX_IMAGE,
        ),
        worker_path=worker,
    )

    result = sandbox.run(
        SandboxRequest(
            run_id="workspace-write",
            tool="safe_noop",
            base_url="http://127.0.0.1",
            parameters={},
            request_timeout_seconds=2,
            network_access="none",
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"

def test_target_repository_is_read_only_inside_docker_sandbox(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "trusted-repository"
    repo.mkdir()

    source = repo / "artifact.txt"
    source.write_text("original", encoding="utf-8")

    worker = tmp_path / "repo_worker.py"

    worker.write_text(
        """
from pathlib import Path

path = Path("/target/artifact.txt")

try:
    path.write_text("modified")
except OSError:
    print("target-read-only")
else:
    raise SystemExit("target repository is writable")
""",
        encoding="utf-8",
    )

    sandbox = DockerSandbox(
        policy=SandboxPolicy(
            backend="docker",
            network_mode="none",
            sandbox_image=TEST_SANDBOX_IMAGE,
        ),
        worker_path=worker,
    )

    result = sandbox.run(
        SandboxRequest(
            run_id="repo-readonly",
            tool="inspect_dockerfile_user",
            base_url="http://127.0.0.1",
            parameters={},
            request_timeout_seconds=2,
            network_access="none",
            repository_path=str(repo),
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "target-read-only"

    assert source.read_text(encoding="utf-8") == "original"

def test_network_none_prevents_external_connections(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "network_worker.py"

    worker.write_text(
        """
import socket

sock = socket.socket()
sock.settimeout(1)

try:
    sock.connect(("1.1.1.1", 53))
except OSError:
    print("network-blocked")
else:
    raise SystemExit("network unexpectedly available")
finally:
    sock.close()
""",
        encoding="utf-8",
    )

    sandbox = DockerSandbox(
        policy=SandboxPolicy(
            backend="docker",
            network_mode="none",
            sandbox_image=TEST_SANDBOX_IMAGE,
        ),
        worker_path=worker,
    )

    result = sandbox.run(
        SandboxRequest(
            run_id="network-none",
            tool="safe_noop",
            base_url="http://127.0.0.1",
            parameters={},
            request_timeout_seconds=3,
            network_access="none",
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "network-blocked"
