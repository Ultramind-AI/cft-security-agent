# Demo Case v0

## Goal

Один finding должен пройти полный workflow.

## Flow

```text
Finding
→ Context
→ Scoring
→ Hypothesis
→ ActionProposal
→ Validator
→ Executor
→ Evidence
→ Re-evaluation
→ FinalReport
```

## Current mode

Пока используется synthetic finding и safe_noop.
Это нужно только для проверки архитектуры и orchestration.
