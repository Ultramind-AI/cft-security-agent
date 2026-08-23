# CI/CD gate v1

The pipeline entrypoint closes the loop between SAST, agentic verification, and a
machine-consumable CI decision.

```text
push / pull request
  -> CI runner
  -> deterministic project discovery
  -> one managed target sandbox session
  -> Semgrep SAST
  -> normalized findings
  -> bounded agent workflow against ready sandbox services
  -> runtime telemetry and Evidence
  -> FinalReport JSON per finding
  -> deterministic gate aggregation
  -> PASS / WARN / FAIL
```

The LLM never decides whether CI passes. It may analyze a finding and propose an
allowlisted verification action, but the final gate is deterministic and reads
only validated `FinalReport` objects.

## Gate policy v1

- `FAIL`: a mandatory stage failed, or a `confirmed` finding has HIGH/CRITICAL
  CVSS severity or HIGH/CRITICAL contextual priority.
- `WARN`: a lower-priority confirmed finding, `inconclusive`, or `policy_blocked`.
- `PASS`: no warning/blocking condition remains, including the no-findings case
  or findings rejected by capability-specific Evidence.

Exit codes:

- `0`: PASS or WARN. WARN is visible but intentionally non-blocking.
- `1`: FAIL caused by a confirmed blocking finding.
- `2`: mandatory pipeline stage failure, for example SAST or agent execution did
  not complete.

`gate.json` exposes mandatory system failures through `errors`, whose entries
contain `code`, `layer`, `message` and `retryable`. The legacy `stage_errors`
array is retained as a human-readable projection of those structured errors.
Validator denial and `policy_blocked` remain normal security-workflow outcomes,
not internal errors.

The exact policy can later be made customer-configurable without changing the
agent/Validator/Executor boundaries.

## One-command local demo

From `cft-security-agent/`, with the controlled SberLab repository next to it:

```bash
CFT_AGENT_MODE=llm CFT_LLM_TRACE=true python -m app.pipeline_run \
  --target ../sberlab_hack \
  --architecture targets/sberlab_architecture.yaml \
  --output-dir artifacts/security-pipeline \
  --max-iterations 1
```

This command runs Semgrep first, then verifies every normalized finding, writes
one FinalReport JSON per finding, and finally writes:

- `artifacts/security-pipeline/sast/findings.json`
- `artifacts/security-pipeline/reports/*.json`
- `artifacts/security-pipeline/reports-index.json`
- `artifacts/security-pipeline/gate.json`

For a faster demo using an already generated findings file:

```bash
CFT_AGENT_MODE=llm CFT_LLM_TRACE=true python -m app.pipeline_run \
  --target ../sberlab_hack \
  --architecture targets/sberlab_architecture.yaml \
  --findings artifacts/sast/findings.json \
  --output-dir artifacts/security-pipeline \
  --max-iterations 1
```

Add `--full-reports` when the terminal demo should print every human-readable
FinalReport instead of only the compact per-finding summary.

## GitHub Actions integration

`.github/workflows/security-pipeline.yml` is the single reusable implementation.
It checks out the complete target into one fixed `target/` location, then uses
discovery and `SandboxManager` instead of target-specific directory commands.
`examples/github-actions/sberlab-security-gate.yml` shows the small caller used by
a target repository on pull requests and pushes.

The profile binds `metadata.ci.repository` to the allowed GitHub repository. The
target checkout has no persisted Git credentials, and the target process receives
an environment with CI tokens, passwords and LLM API keys removed. Provider keys
remain available only to the agent process.

The reusable job is named `Security gate`; configure that stable check as required
in the target repository branch protection. Artifacts are uploaded with
`if: always()` and include discovery, runtime service map, SAST, reports, Gate,
audit records and runtime telemetry, including technical-failure runs.
