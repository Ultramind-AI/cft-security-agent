# Architecture

```text
SAST + Code + Architecture
          ↓
       Scoring
          ↓
        Agent
          ↓
    ActionProposal
          ↓
       Validator
      /         \
  APPROVE       DENY
    ↓             ↓
 Executor     Policy blocked
    ↓             ↓
 Evidence ────────┘
    ↓
 Re-evaluation
    ↓
 Final report
```

## Responsibility boundaries

### Agent

Reasoning and orchestration.

### Validator

Deterministic permission decision.

### Executor

Controlled execution of registered capabilities. The current MVP adds a fixed
worker process, a disposable per-run directory, OS resource limits, bounded
output, persistent evidence and an append-only audit event. Timeout or worker
failure returns data to the graph instead of raising through the pipeline.

### Scoring

CVSS + Context Priority.

Context Priority consumes only the universal `ArchitectureContext`; it does not
know target names such as `backend` or `frontend`. The current producer is:

```text
ProjectDescription (services and connections)
  → ProjectDescriptionAdapter
  → automatically derived ArchitectureContext
  → optional YAML overrides
  → existing deterministic ScoringService
```

The project-description YAML keeps the existing `services` format. Optional
overrides use context field names and replace only explicitly supplied facts:

```yaml
services:
  payments:
    public_exposure: true
    criticality: critical
    authentication: none
    blast_radius: shared
```

Use `--architecture-overrides path/to/overrides.yaml` with `app.e2e_demo` or
`app.pipeline_run`. A future Discovery/TargetProfile producer can construct a
`ProjectDescription` in memory and reuse `ProjectDescriptionAdapter`; the
scoring formula does not need to change.

### Explainable report and CI decision

`FinalReport` is the single machine-readable and human-renderable audit artifact for
one finding. In addition to the existing verdict and Evidence, it records the
original source description, code and architecture context, agent hypothesis,
proposed sandbox action, Validator decision, execution outcome, limitations, and
the deterministic per-finding CI Gate effect.

The gate keeps separate decision categories: `confirmed_risk`, `policy_block`,
`inconclusive`, and `technical_pipeline_error`. A policy denial remains a normal
report outcome; a mandatory stage failure remains a pipeline error and is never
reported as a confirmed vulnerability.

### Pull Request awareness

`app.pipeline_run` accepts `--base-ref`, `--head-ref`, and `--base-findings`.
The PR layer reads a zero-context Git diff, maps head-side changed lines, and
matches base/head findings by a SHA-256 fingerprint that excludes unstable line
numbers. Each head finding is classified as `new`, `existing`, or
`affected-by-change`; an optional `--base-architecture` also detects changes in
its universal architecture context. The result is written to `pr-analysis.json`
and embedded in the finding report. Under PR policy a new or affected confirmed
HIGH risk blocks, while the same explicitly pre-existing risk produces a warning.
