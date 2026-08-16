# Security tool v0.1: backend Dockerfile USER check

The first real verification capability is intentionally narrow:

```text
check_sberlab_backend_dockerfile_user
```

It exists only for the controlled SberLab E2E finding produced by the Semgrep
`dockerfile.security.missing-user` rule for `backend/Dockerfile`.

## Security boundary

The agent cannot provide a command, path, Dockerfile name, repository root or shell text.
The `ActionProposal` for this capability accepts no parameters. Validator must approve the
registered capability first. Executor then supplies the trusted local repository path from
operator configuration to the fixed worker.

The worker reads exactly:

```text
backend/Dockerfile
```

inside that trusted repository root. Absolute/relative paths supplied by the agent are not
part of the contract.

## Evidence semantics

The worker inspects Dockerfile instructions in the final build stage and emits structured
JSON matching `DockerfileUserCheckResult`.

```text
final stage has no explicit USER -> confirmed
final stage has explicit USER    -> rejected
file unavailable / malformed     -> inconclusive
```

`confirmed` here means that the **reported source condition** is present: the final local
Dockerfile stage does not explicitly set `USER`.

This does **not** claim that a running container was proven to have UID 0. A base image can
carry its own user metadata, so runtime identity would require a different capability and a
different Evidence claim. The output therefore records:

```text
scope = source
runtime_user_verified = false
```

This distinction prevents the first demo from overstating what its Evidence proves.
