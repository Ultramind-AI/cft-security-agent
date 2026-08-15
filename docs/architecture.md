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
Owns reasoning and orchestration. Does not execute arbitrary actions.

### Validator
Owns permission decisions. Must be deterministic for hard restrictions.

### Executor
Owns controlled execution. Only runs registered approved capabilities.

### Scoring
Owns CVSS and Context Priority. CVSS is technical severity. Context Priority is system-specific importance.

## First target
SberLab is a local defensive test target and stays separate from the Agent repository.
