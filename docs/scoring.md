# Scoring integration v0.1

This stage replaces the scoring placeholder used by the first real E2E run.

## Context Priority v0.1

Context Priority is deterministic and stays separate from CVSS. The score uses only
architecture facts that are present in `ArchitectureContext`.

| Signal | v0.1 points |
|---|---:|
| Internet/public exposure | +2 |
| Critical/high asset | +2 |
| Medium/normal asset | +1 |
| Direct database connection | +2 |
| Direct path to a high/critical connected service | +1 |
| No authentication | +1 |
| Ordinary authenticated user | +0.5 |
| Admin requirement | +0 |
| Many/shared blast radius | +2 |
| Several/limited blast radius | +1 |
| Isolated blast radius | +0 |

Thresholds:

```text
0..3   -> LOW
>3..6  -> MEDIUM
>6     -> HIGH
```

Unknown authentication or blast-radius data contributes `0` and is kept explicitly in
`reasons`. This is deliberate: the scorer must not invent architecture facts that are not
provided yet.

`ArchitectureService` derives a direct critical path when a service directly connects to a
node marked `high` or `critical`. For the current SberLab backend, the explicit architecture
facts produce:

```text
public exposure        +2
backend criticality    +2
direct database access +2
backend -> database    +1
authentication unknown +0
blast radius unknown   +0
--------------------------
Context Priority        7 / HIGH
```

The security-tools specification can later make authentication and blast-radius values
explicit in `targets/sberlab_architecture.yaml` without changing the scoring interface.

## CVSS 4.0 handling

The first E2E candidate is the Semgrep Docker hardening rule
`dockerfile.security.missing-user.*`.

For this finding, v0.1 returns:

```text
vector   = N/A
score    = null
severity = N/A
```

A missing container `USER` directive is treated as a hardening/configuration finding until
there is a demonstrated vulnerability and justified CVSS metrics. Assigning a numeric score
from Semgrep severity would create false precision.

Other findings remain `UNASSESSED` until explicit CVSS 4.0 metrics are supplied. Semgrep
`ERROR` / `WARNING` is never converted directly into a CVSS score.

This does not yet implement the final numeric CVSS calculator for arbitrary findings. That
calculator belongs behind the already-defined `calculate_cvss` tool contract and must use
explicit metrics plus deterministic code/library.
