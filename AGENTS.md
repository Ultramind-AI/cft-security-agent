# AGENTS.md

## 0. Purpose

This file is the canonical implementation context for coding agents and contributors working on `cft-security-agent`.

Before changing shared schemas, `LangGraph`, the reasoning layer, scoring, `Validator`, `Executor`, Evidence handling, tools or SberLab integration, read this file.

Priority of truth:

```text
working code + tests
→ current Miro architecture / workflow
→ AGENTS.md
→ README.md
```

If implementation and documentation diverge, preserve the security invariants first, then synchronize documentation.

---

# 1. Project goal

Task:

**«ИИ-агент для поиска уязвимостей в ПО в CI/CD-контуре».**

The system combines:

1. contextual vulnerability scoring;
2. a bounded agent workflow for controlled verification.

High-level flow:

```text
Developer / PR
→ CI/CD
→ SAST
→ contextual scoring
→ pentest agent
→ Validator
→ Executor
→ Evidence
→ FinalReport
→ CI/CD decision
```

---

# 2. Miro is the visual architecture reference

Board:

```text
https://miro.com/app/board/uXjVHztE6Mg=/
```

Architecture frame v0.2:

```text
https://miro.com/app/board/uXjVHztE6Mg=/?moveToWidget=3458764680317736055
```

Agent workflow frame v0.1:

```text
https://miro.com/app/board/uXjVHztE6Mg=/?moveToWidget=3458764680324185356
```

Kanban:

```text
https://miro.com/app/board/uXjVHztE6Mg=/?moveToWidget=3458764680554208524
```

README diagrams under `docs/diagrams/` must preserve the same logical blocks and connections as these Miro frames. Styling may differ; topology must not.

---

# 3. Exact architecture represented in Miro v0.2

## 3.1. Main pipeline

```text
Разработчик / PR
→ CI/CD-пайплайн
→ SAST-анализ
→ Контекстная оценка
→ Агент-пентестер
→ Валидатор
```

Validator branches:

```text
APPROVE
→ Исполнитель
→ Доказательства / результаты
→ Агент-пентестер
```

and:

```text
DENY
→ Отклонено политикой
→ Финальный отчёт
```

Evidence/results also go to:

```text
Доказательства / результаты
→ Финальный отчёт
```

Final output:

```text
Финальный отчёт
→ Решение для CI/CD
→ CI/CD-пайплайн
```

## 3.2. Supporting inputs

```text
Архитектурный граф
→ Контекстная оценка
```

```text
Репозиторий / код
→ Агент-пентестер
```

```text
Политика разрешений
→ Валидатор
```

## 3.3. Architecture labels

The Miro architecture currently contains these named blocks:

```text
Архитектурный граф
Репозиторий / код
Политика разрешений
Разработчик / PR
CI/CD-пайплайн
SAST-анализ
Контекстная оценка
Агент-пентестер
Валидатор
Исполнитель
Доказательства / результаты
Отклонено политикой
Финальный отчёт
Решение для CI/CD
```

Do not silently add another execution path that bypasses this structure.

---

# 4. Exact agent workflow represented in Miro v0.1

Main sequence:

```text
Состояние агента
→ 1. Загрузка контекста
→ 2. Анализ и приоритизация
→ 3. Формирование гипотезы
→ 4. План проверки
→ 5. Валидатор
```

Approved branch:

```text
5. Валидатор
→ APPROVE
→ 6. Исполнение
→ 7. Сбор evidence
→ 8. Переоценка
→ 9. Стоп-условие?
```

Stop condition:

```text
ДА, завершить
→ Финальный отчёт
```

or:

```text
ещё одна итерация
→ 3. Формирование гипотезы
```

Denied branch:

```text
5. Валидатор
→ DENY
→ Policy blocked
→ Финальный отчёт
```

Tool inputs:

```text
Инструменты чтения
→ 1. Загрузка контекста
```

```text
Инструменты оценки
→ 2. Анализ и приоритизация
```

```text
Инструменты проверки
→ 6. Исполнение
```

---

# 5. Miro ↔ implementation mapping

The code does not need to use the same human-facing labels, but it must preserve the same semantics.

| Miro step | Current implementation |
|---|---|
| Состояние агента | `AgentState` |
| 1. Загрузка контекста | `load_context` |
| 2. Анализ и приоритизация | `score_finding` + `analyse` |
| 3. Формирование гипотезы | `form_hypothesis` |
| 4. План проверки | `propose_action` |
| 5. Валидатор | `validate_action` |
| 6. Исполнение | `execute_action` / graph node `execute` |
| 7. Сбор evidence | `collect_evidence` |
| 8. Переоценка | `reevaluate` |
| 9. Стоп-условие | conditional edge after `reevaluate` |
| Финальный отчёт | `build_report` / graph node `report` |
| Policy blocked | `status="policy_blocked"` → `report` |

Current graph implementation may loop from `reevaluate` to `analyse`; the conceptual Miro loop points to hypothesis formation. When the reasoning nodes become real LLM-backed components, keep the logical behavior aligned: a new iteration must reconsider the hypothesis/proposal using accumulated evidence.

---

# 6. Core security invariants

The architecture is built around one rule:

```text
Agent думает
Validator разрешает
Executor выполняет
Evidence определяет результат
```

Mandatory invariants:

1. Agent never gets arbitrary shell execution.
2. Agent never directly invokes an execution capability.
3. Every active `ActionProposal` passes through `Validator`.
4. `Validator` decisions are deterministic code/policy, not prompt-only rules.
5. `Executor` can run only registered capabilities.
6. `confirmed` requires Evidence.
7. Agent cannot expand target scope.
8. Active checks run only against approved local / sandbox / staging environments.
9. The workflow has an iteration limit.
10. Production target access is out of scope for the MVP.

---

# 7. Shared contracts

Canonical project models:

```text
Finding
ArchitectureContext
CVSSResult
ContextPriority
AnalysisResult
Hypothesis
ActionProposal
ValidationResult
ExecutionResult
Evidence
ReevaluationResult
FinalReport
AgentState
```

They live in `schemas/`.

Do not create component-local duplicates of these contracts.

If a component needs additional data, first check whether the existing contract can express it. Shared schema changes must consider every consumer.

---

# 8. AgentState

Current state fields:

```text
finding
code_context
architecture_context

cvss
context_priority

analysis
hypothesis
proposed_action

validation
execution
evidence

status
iteration_count
max_iterations

final_report
```

`AgentState` is the shared memory of a single workflow run.

Do not store provider-specific live SDK objects or non-serializable runtime handles in state.

---

# 9. Current LangGraph implementation

Current executable topology:

```text
START
→ load_context
→ score_finding
→ analyse
→ form_hypothesis
→ propose_action
→ validate_action
```

Validation branch:

```text
approved
→ execute
→ collect_evidence
→ reevaluate
```

```text
denied
→ report
→ END
```

Re-evaluation branch:

```text
continue
→ analyse
```

```text
terminal status
→ report
→ END
```

Terminal statuses:

```text
confirmed
rejected
inconclusive
policy_blocked
```

The code is a testable implementation of the Miro workflow. Human-facing README diagrams mirror Miro; implementation names remain code-oriented.

---

# 10. Reasoning layer

`agent/model.py` defines the `AgentReasoningModel` protocol.

Methods:

```text
analyse(state) -> AnalysisResult

form_hypothesis(
    state,
    analysis,
) -> Hypothesis

propose_action(
    state,
    analysis,
    hypothesis,
) -> ActionProposal

reevaluate(state) -> ReevaluationResult
```

Current implementation:

```text
DeterministicAgentModel
```

Purpose:

- no external API;
- deterministic local tests;
- prove graph integration before connecting a real LLM.

A future LLM adapter must implement the same interface. Do not rewrite LangGraph nodes around a provider SDK.

---

# 11. System prompt

`agent/prompts.py` defines behavioral rules for the reasoning model.

It must contain:

- mission;
- allowed information sources;
- separation of facts vs assumptions;
- hypothesis requirements;
- `ActionProposal` requirements;
- no direct execution;
- scope/policy constraints;
- Evidence requirements;
- stop conditions;
- terminal output rules.

The prompt is not a security boundary.

Security boundaries are:

```text
Validator
Policy
Executor registry
Runtime limits
```

---

# 12. Scoring

## 12.1. CVSS 4.0

Purpose:

```text
technical severity
```

Final implementation must return:

```text
vector
score
severity
reasoning
```

The final numeric score must be produced by deterministic code/library.

The LLM may help propose justified metric values, but it must not invent the final score.

## 12.2. Context Priority

Purpose:

```text
importance of the finding in this architecture
```

Current Miro design by Лёха considers signals such as:

```text
public exposure
asset criticality
database access
path to critical services
authentication / privileges
blast radius
```

Keep CVSS and Context Priority separate.

Do not add them together into a synthetic score.

---

# 13. Validator

Expected input:

```text
ActionProposal
```

Expected output:

```text
ValidationResult
```

The final Validator should enforce at least:

```text
target allowlist
tool allowlist
environment restrictions
scope
required parameters
time limits
iteration / run limits
logging requirements
blocked action classes
```

The starter validator currently proves the interface and deny path; Рома's policy work should replace/extend it without changing the graph boundary.

---

# 14. Executor

Executor responsibilities:

```text
receive approved ActionProposal
verify approval matches action_id
resolve registered capability
execute inside allowed environment
return structured ExecutionResult
```

Executor must not:

```text
plan
reinterpret the goal
accept arbitrary shell text from LLM
execute unknown tools
bypass Validator
```

Current `safe_noop` is a deterministic integration stub.

---

# 15. Evidence

Evidence is the bridge from execution back to reasoning.

Expected terminal logic:

```text
confirmed     -> sufficient supporting evidence
rejected      -> sufficient contradicting evidence
inconclusive  -> evidence remains insufficient
policy_blocked -> required action was denied
```

The final Evidence criteria are owned by Рома.

An LLM conclusion alone is not Evidence.

---

# 16. Target v1: SberLab

SberLab is the first controlled target.

Agent and target remain separate:

```text
workspace/
├── cft-security-agent/
└── sberlab-target/
```

Rules:

1. Do not move agent logic into SberLab.
2. Do not intentionally inject vulnerabilities for the demo.
3. Active checks are local/test only.
4. Runtime stabilization should not rewrite security-sensitive application behavior unnecessarily.
5. Target metadata belongs in `targets/`.

---

# 17. Current Miro Kanban snapshot

Miro is the source of truth for current team status.

## Done

### Ставр

```text
Создать skeleton cft-security-agent
Описать AgentState и LangGraph workflow
Системный промпт агента
```

### Лёха

```text
Разобраться с CVSS 4.0
Спроектировать Context Priority
```

### Общее

```text
Зафиксировать SberLab как Target v1
```

## In progress

### Кирилл

```text
Стабильно поднять SberLab в Docker
```

## To do / blocked by dependencies

### Ставр

```text
Зафиксировать контракты tools
Собрать один сквозной демо-проход
```

### Рома

```text
Спроектировать Validator / permission policy
Определить критерии Evidence
Составить список security tools
```

### Кирилл

```text
Сделать прототип Executor
Добавить ограничения и логирование Executor
Набросать CI/CD интеграцию
```

### Лёха

```text
Разобрать 3–5 findings из SberLab
```

### Общее

```text
Выбрать 1 finding для end-to-end сценария
Собрать Architecture v0.3 в Miro
```

Dependency notes:

```text
SberLab Docker
→ first SAST
→ 3–5 real findings
→ choose demo finding
→ integrate scoring / validator / executor
→ end-to-end
→ Architecture v0.3
```

---

# 18. Team ownership

```text
Ставр
  AgentState
  LangGraph
  reasoning/model layer
  shared integration
  tool contracts after security inputs are known

Лёха
  CVSS 4.0
  Context Priority
  scoring examples on real findings
  later scoring implementation

Рома
  Validator permission policy
  Evidence criteria
  security tool list

Кирилл
  SberLab Docker/runtime
  Executor
  execution limits/logging
  CI/CD sketch
```

---

# 19. Git workflow

`main` is the stable shared base.

Flow:

```text
main
→ feature/<task>
→ implementation
→ tests
→ Pull Request
→ review
→ merge
→ main
```

Rules:

1. New branches start from up-to-date `main`.
2. Do not keep completed feature branches unmerged without reason.
3. Run the full test suite before merge.
4. Changes to shared schemas require awareness of downstream consumers.
5. Each contributor should deliver their component through the shared contracts rather than inventing a parallel interface.

---

# 20. Tests

Local checks:

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
```

Current expected result:

```text
9 passed
```

Covered behavior:

```text
safe Executor roundtrip
unknown tool denial
confirmed workflow
rejected workflow
policy_blocked workflow
inconclusive loop
structured deterministic analysis
deterministic ActionProposal
system prompt security boundaries
```

Manual graph check:

```bash
python3 -m app.main
```

---

# 21. Next development order

Current correct order:

```text
1. Keep merged Agent/LangGraph/model work stable in main.
2. Finish SberLab Docker/runtime.
3. Finish Validator policy, Evidence criteria and security tool list.
4. Run first SAST against SberLab.
5. Review 3–5 real findings.
6. Complete CVSS/Context Priority implementation using real inputs.
7. Select one safe demo finding.
8. Replace starter Validator/Executor/scoring stubs with real components.
9. Run one end-to-end workflow.
10. Update Architecture v0.3 in Miro from the working system.
```

Do not invent new architecture work merely to fill waiting time. Integrate only when the required component is ready.

---

# 22. Explicit non-goals for the MVP

Do not add without a concrete need:

```text
multi-agent architecture
GNN
Kubernetes
vector database
large RAG pipeline
large pentest tool catalog
arbitrary code execution
production attack automation
production target access
```

The MVP is one understandable, reproducible, safe end-to-end case.

---

# 23. Definition of Done

The MVP is complete when one real SberLab finding passes:

```text
SAST
→ Finding
→ Code Context
→ Architecture Context
→ CVSS
→ Context Priority
→ AnalysisResult
→ Hypothesis
→ ActionProposal
→ ValidationResult
→ ExecutionResult
→ Evidence
→ Re-evaluation
→ FinalReport
```

and:

```text
every active action passed Validator
Executor used only registered capabilities
terminal result is supported by Evidence
the run is reproducible
every step is explainable during the demo
```
