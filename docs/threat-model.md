# Security Sandbox Threat Model

The sandbox isolates capability execution from the host filesystem, CI secrets,
Docker socket, external networks, and neighbouring Compose projects.

## Trust boundaries

- The agent never supplies shell, host paths, arbitrary URLs, or Docker arguments.
- Executor accepts registered capabilities and target-owned metadata only.
- Target repositories are mounted read-only for capability containers.
- Docker sandbox images are immutable digest references and use a non-root user,
  `cap-drop=ALL`, `no-new-privileges`, read-only root filesystem, and bounded tmpfs.

## Network and resources

The default network is `none`. A capability may reach the target only through a
configured internal network. CPU, memory, PIDs, wall time, output, and workspace
size are bounded by `SandboxPolicy`; CI secrets are not inherited.

## Compose sessions

`SandboxSession` accepts only a trusted `TargetProfile`. It rejects external
Compose networks and volumes and host bind mounts. Each session has its own
Compose project name and therefore its own containers, networks, and volumes.
Teardown uses that project name and, only after `compose down` fails, the exact
`com.docker.compose.project` label. It never uses global Docker prune commands.
The absence of leftovers is accepted only after successful Docker queries for
containers, networks, and volumes.

The Compose file is an operator-owned relative path in `TargetProfile.runtime`;
readiness paths come from declared service healthchecks. The session does not
infer a backend/frontend role or accept a repository path from the agent.

If Docker isolation cannot be established where it is required, execution fails
closed rather than falling back to a weaker boundary.
