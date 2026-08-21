# Sandbox session

`executor.sandbox_session.SandboxSession` manages one local, trusted Docker
Compose target from preparation through cleanup. It is intentionally small and
is suited to local development and CI, not orchestration of production targets.

```python
from executor.sandbox_session import SandboxSession

with SandboxSession(target) as session:
    state = session.collect_state()
    assert state["ready"]
```

The states are `created`, `preparing`, `starting`, `ready`, `tearing_down`,
`closed`, `failed`, and `timed_out`.

Preparation verifies the target directory and configured Compose file, runs
`docker compose config`, and rejects external networks, external volumes, and
host bind mounts. Startup uses a unique `cft-sandbox-<session-id>` project and
waits for the target-owned fixed `/health/` endpoint. `compose up --build` has
its own bounded `startup_timeout` (300 seconds by default); short configuration,
diagnostic, teardown, and label-check commands keep `command_timeout`.

On normal completion, startup failure, health timeout, or an exception inside
the context manager, cleanup runs `docker compose down --volumes --remove-orphans`
in `finally`. If that command fails, fallback cleanup is limited to resources
with the exact Compose project label. The session then checks containers,
networks, and volumes with that label and reports any remaining names.
Failure of any Docker resource query is also a cleanup failure; it is never
interpreted as an empty result.

Run unit tests with `python -m pytest tests/test_sandbox_session.py -q`.
The Docker/SberLab integration test reads `SBERLAB_TARGET_PATH`; it skips only
when a Docker daemon or the target checkout is unavailable.

The security boundary includes the unique project, a session-local Docker
network and volumes, no Docker socket in capability containers, read-only target
mounts, policy resource limits, and no inherited CI secrets.
