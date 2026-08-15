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
