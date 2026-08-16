# Real E2E foundation v0.1

This stage connects the existing workflow to real project inputs without pretending that
execution success is the same thing as vulnerability confirmation.

## What is real now

`app.e2e_demo` can load:

```text
normalized reports/sast/findings.json
→ one real Finding
→ bounded source context from the configured local target repository
→ ArchitectureContext from targets/sberlab_architecture.yaml
→ existing scoring service
→ existing Agent / Validator / Executor / Evidence workflow
→ FinalReport
```

The source reader is bounded to one configured target root. Paths from Semgrep may contain
Windows separators, but absolute paths and `..` escapes are rejected.

## Important verdict rule

A successful Executor run is only an execution fact. It is not enough to mark a finding
`confirmed`.

The deterministic `safe_noop` fixture may still produce `TEST_CONFIRMED`, `TEST_REJECTED`
or `TEST_INCONCLUSIVE` for synthetic tests. Real SAST severities such as `ERROR` and
`WARNING` now default to `inconclusive` until a capability-specific Evidence interpreter
exists.

This is intentional. The next security integration step is to select one finding, define
its verification requirement and Evidence criteria, and map it to one fixed safe Executor
capability.

## Local run

First produce normalized findings if needed:

```bash
python -m app.sast_scan --target ../sberlab_hack
```

Then run one selected finding through the current workflow:

```bash
python -m app.e2e_demo \
  --findings reports/sast/findings.json \
  --target ../sberlab_hack \
  --index 0
```

For the first Docker hardening candidate, index `0` is expected only if the local SAST
report still has that finding first. Prefer `--finding-id` for a stable demo script.

Until the matching verification capability and Evidence semantics are implemented, a real
finding is expected to finish as `inconclusive`, not `confirmed`.

## Scoring status after v0.1 integration

The first real Docker hardening finding no longer produces placeholder scoring:

```text
CVSS: N/A
Context Priority: HIGH (7.0)
```

`N/A` is intentional for this hardening finding. Other CVSS-applicable findings remain
`UNASSESSED` until explicit metrics are available. See `docs/scoring.md`.
