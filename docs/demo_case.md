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

Полный LangGraph-проход пока использует synthetic finding и `safe_noop`. Это
нужно только для детерминированной проверки архитектуры и orchestration.

Executor отдельно имеет рабочий локальный demo-проход:

```text
ActionProposal(observe_http_surface, service=backend, endpoint=/health/)
→ Validator
→ ApprovalRecord
→ Executor
→ bounded worker process
→ fixed GET to an endpoint from RuntimeServiceMap
→ ExecutionResult
→ JSON evidence + JSONL audit
→ agent reads JSON evidence by evidence_ref
```

Запуск при поднятом SberLab backend:

```bash
python3 -m app.executor_demo
```
