# AGENTS.md

## Назначение

Этот файл является главным архитектурным контекстом проекта для AI coding agents и разработчиков.

Перед изменением архитектуры, schemas, workflow, Validator, Executor, scoring или target integration сначала прочитать этот файл.

---

# 1. Контекст задачи

ЦФТ поставил задачу:

**ИИ-агент для поиска уязвимостей в ПО в CI/CD-контуре.**

Есть две связанные части.

## Часть 1. Контекстный скоринг

На вход:

- архитектурный граф;
- результаты статического анализа;
- кодовая база и метаданные.

Нужно оценить важность finding с учётом контекста.

Примеры сигналов:

- public exposure;
- trust zone;
- критичность сервиса;
- связь с БД;
- путь до критичных компонентов;
- blast radius.

## Часть 2. ИИ-агент-пентестер

Агент должен:

```text
получить finding
→ загрузить контекст
→ проанализировать
→ сформировать hypothesis
→ предложить ActionProposal
→ пройти Validator
→ выполнить разрешённую проверку через Executor
→ получить Evidence
→ переоценить finding
→ завершить или сделать ещё итерацию
```

---

# 2. Базовая архитектура

Главное правило:

```text
Agent думает
Validator разрешает
Executor выполняет
```

## Agent

Agent:

- читает finding;
- читает код;
- получает архитектурный контекст;
- формирует hypothesis;
- предлагает ActionProposal;
- читает Evidence;
- решает, нужна ли новая итерация;
- формирует FinalReport.

Agent не должен выполнять произвольные действия напрямую.

## Validator

Validator:

- получает ActionProposal;
- проверяет target;
- проверяет environment;
- проверяет allowlist tools;
- проверяет лимиты;
- проверяет scope;
- возвращает APPROVE/DENY.

Критичные правила должны быть детерминированным кодом/конфигом.

## Executor

Executor:

- принимает только одобренный ActionProposal;
- работает через registry заранее известных действий;
- не планирует;
- не интерпретирует цель;
- возвращает ExecutionResult.

---

# 3. MVP

Первая цель:

```text
один finding
→ полный end-to-end проход
→ FinalReport
```

Не нужно сначала строить огромную систему.

---

# 4. Первый target

Первый target:

```text
SberLab
```

Правила:

- не добавлять туда специально уязвимости;
- не смешивать код target и код agent;
- активные проверки только на локальной/тестовой копии;
- target подключается через `targets/sberlab.yaml`.

---

# 5. Shared contracts

Канонические модели:

```text
Finding
ArchitectureContext
CVSSResult
ContextPriority
Hypothesis
ActionProposal
ValidationResult
ExecutionResult
Evidence
FinalReport
AgentState
```

Они находятся в `schemas/`.

Не создавать локальные дубли этих моделей.

---

# 6. LangGraph

Текущий workflow:

```text
START
  ↓
load_context
  ↓
score_finding
  ↓
analyse
  ↓
form_hypothesis
  ↓
propose_action
  ↓
validate_action
  ↓
approved?
  ├─ no → report → END
  └─ yes
      ↓
    execute
      ↓
 collect_evidence
      ↓
  reevaluate
      ↓
    continue?
   ├─ yes → analyse
   └─ no  → report → END
```

AgentState хранит:

```text
finding
code_context
architecture_context
cvss
context_priority
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

---

# 7. System prompt

System prompt управляет поведением модели, но не является security boundary.

Безопасность обеспечивают:

```text
Validator
Policy
Executor registry
Limits
```

---

# 8. Scoring

CVSS и Context Priority разделены.

## CVSS

Техническая severity.

Итоговый score должен считаться детерминированно кодом.

## Context Priority

Приоритет внутри конкретной архитектуры.

Сигналы:

- public exposure;
- критичность;
- database connectivity;
- critical path;
- trust zone;
- blast radius.

---

# 9. Tool architecture

Tool = typed capability.

Каждый tool должен иметь:

- name;
- purpose;
- input;
- output;
- permissions;
- errors;
- side effects;
- environment limitations.

Execution tools не выполняются Agent напрямую.

---

# 10. Validator

Validator обязан проверять:

- target allowlist;
- tool allowlist;
- environment;
- параметры;
- limits;
- logging requirement;
- scope.

Validator возвращает ValidationResult.

---

# 11. Executor

Executor:

- не принимает raw shell command от LLM;
- не исполняет unknown tools;
- проверяет approval;
- проверяет action_id;
- возвращает structured result.

---

# 12. Evidence

ExecutionResult нормализуется в Evidence.

Final statuses:

```text
confirmed
rejected
inconclusive
policy_blocked
```

Confirmed требует evidence.

---

# 13. Порядок разработки

1. Skeleton + contracts.
2. AgentState + LangGraph.
3. Стабильный SberLab runtime.
4. Первый SAST.
5. Реальный CVSS / Context Priority.
6. Реальный Validator.
7. Реальный Executor.
8. Один end-to-end finding.
9. Только потом расширение.

---

# 14. Что не делать сейчас

Не добавлять без необходимости:

```text
GNN
multi-agent
Kubernetes
vector DB
large RAG
production attack automation
arbitrary code execution
large tool catalog
```

---

# 15. Git

`main` должен быть рабочим.

Примеры веток:

```text
feature/agent-graph
feature/cvss
feature/context-priority
feature/validator
feature/executor
feature/sberlab-runtime
```

---

# 16. Definition of Done

Первый технический этап готов, когда:

1. один finding нормализован;
2. context загружен;
3. scoring выполнен;
4. hypothesis сформирована;
5. ActionProposal создан;
6. Validator решил APPROVE/DENY;
7. Executor выполнил разрешённую проверку;
8. Evidence создан;
9. Agent сделал re-evaluation;
10. FinalReport сформирован.
