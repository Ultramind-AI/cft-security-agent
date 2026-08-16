# CI/CD gate v1

The pipeline entrypoint closes the loop between SAST, agentic verification, and a
machine-consumable CI decision.

```text
push / pull request
  -> CI runner
  -> Semgrep SAST
  -> normalized findings
  -> bounded agent workflow for every finding
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

`examples/github-actions/sberlab-security-gate.yml` is a target-repository
workflow template. Copy it to SberLab as `.github/workflows/cft-security-gate.yml`.
Then every pull request and every push to `main` can invoke the agent automatically.

The workflow deliberately checks out the target and the security agent into
separate directories. If the agent repository is private, configure
`CFT_AGENT_REPO_TOKEN` with read access. Provider API keys are GitHub Actions
secrets and must never be committed.
