# Contracts v1 and extension points

Статус: T01 / architecture baseline for v1.0.

Этот документ фиксирует границы ядра перед переходом от SberLab-specific MVP к универсальному агенту. Он не реализует `TargetProfile`, `Project Discovery`, runtime sandbox или новый agent loop. Эти изменения идут в следующих задачах. Здесь фиксируется, что остаётся core, что становится конфигурацией и где нужны адаптеры.

## 1. Текущий стабильный pipeline

```text
SAST
→ Finding
→ CodeContext + ArchitectureContext
→ CVSS + ContextPriority
→ AnalysisResult
→ Hypothesis
→ ActionProposal
→ Validator
→ ExecutionResult
→ Evidence
→ deterministic evidence interpretation / reevaluation
→ FinalReport
→ GateResult
→ PASS / WARN / FAIL
```

Для v1 эта цепочка остаётся основой. Универсализация добавляет новые слои вокруг неё:

```text
TargetProject
→ TargetProfile
→ Project Discovery
→ ... existing core ...
→ disposable Security Sandbox
→ Sandbox Agent Runner
→ Runtime Evidence
```

## 2. Stable v1 contracts

### TargetProject

**Статус:** новый core-контракт.

Сейчас identity target размазана между CLI arguments, `Settings`, `TargetDefinition`, `TargetRegistry` и YAML. В v1 один pipeline run должен явно относиться к одному target.

Минимальная ответственность:

```text
TargetProject
- id
- repository_root
- profile / manifest reference
```

Docker/Compose, сервисы, build/run, healthcheck и runtime limits сюда не добавляются. Это ответственность `TargetProfile` из T02.

### Finding

**Текущий код:** `schemas/finding.py`.

**Решение:** оставить core.

`Finding` хранит нормализованный результат SAST. SAST не должен знать архитектуру конкретного проекта. Поле `service` может оставаться optional enrichment, но определение сервиса не должно зависеть от каталогов `backend/` и `frontend/` внутри normalizer.

Producer в v1:

```text
SAST normalizer
+ Project Discovery / service mapping
→ Finding
```

### CodeContext

**Текущий код:** `tools/contracts.py::CodeContextResult`, `schemas/state.py::AgentState.code_context`.

**Решение:** сделать один стабильный core-контракт.

Сейчас одна сущность представлена двумя способами: `CodeContextResult` и `str | None` в `AgentState`. В v1 используем одну структуру:

```text
CodeContext
- file
- line_start
- line_end
- content
```

Путь данных:

```text
LocalCodeReader → CodeContext → AgentState → reasoning model
```

### ArchitectureContext

**Текущий код:** `schemas/architecture.py`.

**Решение:** оставить core.

Контракт уже не зависит от имени проекта:

```text
service
public_exposure
criticality
trust_zone
connected_services
databases
critical_paths
authentication
blast_radius
```

Меняется producer. Сейчас данные в основном приходят из `targets/sberlab_architecture.yaml`. В v1:

```text
Project Discovery
+ TargetProfile
+ optional YAML override
→ ArchitectureContext
```

### CVSSResult / ContextPriority

**Текущий код:** `schemas/scoring.py`, `scoring/service.py`.

**Решение:** оставить core.

`ScoringService` получает только `Finding` и `ArchitectureContext` и не должен знать имя репозитория. CVSS остаётся детерминированным и не заполняет неизвестные метрики догадками.

### AnalysisResult

**Текущий код:** `schemas/agent_outputs.py`.

**Решение:** оставить core.

Это structured reasoning result между facts/context и дальнейшим расследованием.

### Hypothesis

**Текущий код:** `schemas/hypothesis.py`.

**Решение:** оставить core, подготовить к provenance.

Для Runtime Evidence v2 понадобится стабильная identity гипотезы, чтобы Evidence можно было связать с конкретной гипотезой. Добавление поля/ID относится к T11/T14, но requirement фиксируется здесь.

### ActionProposal

**Текущий код:** `schemas/action.py`.

**Решение:** оставить главным контрактом между reasoning и security boundary.

Security invariant:

```text
LLM → ActionProposal → Validator → execution
```

Прямого пути `LLM → Executor` быть не должно.

Текущие поля:

```text
id
tool
target
environment
iteration
parameters
purpose
expected_evidence
```

В v1 `target`, доступные capability и параметры должны приходить из текущего target/session, а не из hardcoded SberLab mapping. Многошаговый `DynamicPlan` строится в T14 поверх этой границы.

### ValidationResult

**Текущий код:** `schemas/validation.py`.

**Решение:** оставить core.

`ValidationResult` является частью официальной границы Agent → Validator → Runner, даже если исходная формулировка T01 его отдельно не перечисляет.

### ExecutionResult

**Текущий код:** `schemas/execution.py`.

**Решение:** оставить core boundary между выполнением и Evidence.

Execution success сам по себе не означает подтверждённую уязвимость.

### Evidence

**Текущий код:** `schemas/evidence.py`, `evidence/interpreter.py`.

**Решение:** сохранить core-инвариант, расширить контракт в T11.

Сейчас:

```text
id
action_id
type
summary
artifact_refs
reliability
verdict
details
```

Для Runtime Evidence v2 понадобятся поля уровня:

```text
source: static | runtime
sandbox_session_id
hypothesis_id
action
scope
observation
timestamp / provenance
artifacts
reliability
```

Главный invariant не меняется: текст LLM не является Evidence.

### FinalReport

**Текущий код:** `schemas/report.py`.

**Решение:** оставить core. Runtime-aware v2 делает T17.

FinalReport должен объяснять полный путь finding до verdict и хранить ограничения анализа отдельно от доказательств.

### GateDecision / GateResult

**Текущий код:** `schemas/pipeline.py`.

**Решение:** оставить deterministic core.

```text
FinalReport → deterministic Gate → PASS / WARN / FAIL
```

LLM может объяснять решение, но не принимает его.

Коды pipeline остаются отдельным контрактом:

```text
0 = pipeline завершён без блокирующей policy ошибки
1 = security policy block / FAIL
2 = technical pipeline failure
```

Security risk и техническое падение pipeline не смешиваются.

## 3. Security invariants

Эти правила считаются стабильными для v1 и не должны ломаться при универсализации:

1. LLM формирует гипотезу и structured action, но не исполняет действия напрямую.
2. Любое действие, выходящее за read-only/scoring, проходит deterministic policy validation.
3. Target и trusted artifacts не принимаются как произвольные пути от LLM.
4. Execution success не равен vulnerability confirmation.
5. Evidence хранит факты отдельно от интерпретации LLM.
6. Terminal capability-specific Evidence не переопределяется LLM.
7. Gate `PASS/WARN/FAIL` остаётся детерминированным.
8. `policy_blocked`, confirmed risk и technical pipeline failure являются разными состояниями.
9. Runtime autonomy в v1 ограничивается sandbox boundary, а не прямым доступом к CI host.
10. Core не должен содержать `if project == ...` или знания о каталогах конкретного репозитория.

## 4. Extension points

### Target profile / manifest

**Назначение:** всё, что относится к конкретному разрешённому target.

Сюда должны переехать:

- repository root;
- services и service types;
- dependency files;
- Docker/Compose metadata;
- build/run strategy;
- healthchecks;
- trusted artifacts;
- разрешённые локальные адреса/endpoints;
- runtime constraints;
- optional architecture overrides.

Текущий `executor/targets.py::TargetDefinition` и `TargetArtifactDefinition` считаются основой для T02, а не кодом на выброс.

### Project Discovery

**Назначение:** детерминированно понимать неизвестный репозиторий.

Discovery отвечает за stack/service detection и кандидатов на build/run. SAST normalizer не должен делать архитектурные выводы по имени каталога.

### Build/Run adapters

**Назначение:** запускать разные типы проектов через единый интерфейс.

Project-specific команды не должны попадать в core pipeline.

### Capability registry / security profiles

**Назначение:** расширять набор проверок без цепочки `if/elif` внутри reasoning model.

Существующие reusable capabilities сохраняются:

- `inspect_dockerfile_user`;
- `inspect_python_password_assignment`;
- `inspect_react_dangerous_html_flow`.

Выбор capability является extension point. Runtime security profiles и новый action contract реализуются позже в T14/T27.

### Sandbox Agent Runner

**Назначение:** выполнить structured action внутри disposable lab и вернуть `ExecutionResult` + audit trail.

Текущий SafeExecutor остаётся MVP boundary, но SberLab-specific runtime tools не являются частью универсального core.

## 5. Refactoring inventory

Ниже список конкретных мест, которые мешают универсальности.

| Модуль / место | Текущая зависимость | Решение v1 | Задача |
|---|---|---|---|
| `app/config.py` | default `targets/sberlab.yaml` | target выбирается конфигом/CLI/registry | T02 |
| `app/pipeline_run.py` | default `../sberlab_hack`, `targets/sberlab_architecture.yaml` | pipeline принимает `TargetProject/TargetProfile` | T02/T19 |
| `app/e2e_demo.py` | SberLab defaults | оставить demo-specific либо перевести на target id | T20 |
| `sast/normalizer.py` | `backend/* → backend`, `frontend/* → frontend` | service mapping через Discovery/Profile | T02/T03 |
| `agent/nodes.py` | fallback `service=backend`, high criticality, `backend -> database` | unknown/partial context, без выдуманной архитектуры | T01/T04 |
| `agent/model.py` | `target="sberlab-local"` | target из текущего run/state | T02/T14 |
| `agent/model.py` | hardcoded Dockerfile → artifact id | artifact resolver / TargetProfile | T02 |
| `agent/model.py` | hardcoded `demo_seed`, frontend/model/serializer/views ids | TargetProfile / security profile | T02/T27 |
| `agent/model.py` | rule → capability через hardcoded branches | capability registry / profile | T14/T27 |
| `agent/llm_model.py` | `sberlab-local` и SberLab runtime tools | available actions из current target/session | T14/T15 |
| `schemas/llm.py` | Literal-список конкретных tools | общий structured action / capability union | T14 |
| `policies/default.yaml` | `sberlab-local` + конкретные artifact ids | разделить global policy и target artifact registry | T02/T06 |
| `targets/sberlab.yaml` | содержит target-specific данные | сохранить как один TargetProfile, расширить schema | T02 |
| `targets/sberlab_architecture.yaml` | ручное описание архитектуры | optional override поверх Discovery | T03/T04 |
| `executor/targets.py` | `TargetDefinition` ограничен MVP runtime/artifacts | эволюционировать в `TargetProfile` | T02 |
| `executor/executor.py` | регистрирует SberLab runtime capabilities | generic runtime capabilities через Runner/Profile | T10/T12 |
| `executor/worker.py` | branches для `check_sberlab_health`, `get_sberlab_public_projects` | legacy/demo или generic trusted runtime probe | T10/T12 |
| `safe_noop`, `force-deny` | integration fixtures | оставить test-only, не считать production architecture | tests |

## 6. Что остаётся core без project-specific логики

```text
Finding
CodeContext
ArchitectureContext
CVSSResult
ContextPriority
AnalysisResult
Hypothesis
ActionProposal
ValidationResult
ExecutionResult
Evidence invariant
FinalReport
GateResult
ArchitectureService
ScoringService
PolicyValidator
```

Project-specific различия должны приходить через config/profile/discovery/adapters.

## 7. Dependency direction v1

Желаемое направление зависимостей:

```text
core schemas
↑
core services
↑
pipeline / agent orchestration
↑
interfaces / registries
↑
target profiles + adapters + sandbox implementation
```

Core не импортирует конкретный SberLab/AutoDealer adapter. Конкретная реализация внедряется через registry/configuration.

## 8. Definition of Done для T01

T01 считается закрытой, когда:

- [x] зафиксирована текущая цепочка от SAST до Gate;
- [x] определён набор стабильных v1 contracts;
- [x] зафиксированы security invariants;
- [x] перечислены extension points;
- [x] найдены основные SberLab-specific места;
- [x] для каждого такого места принято решение: core / config / adapter / legacy/test-only;
- [x] составлен refactoring inventory по модулям;
- [ ] решения синхронизированы с T04/T06 перед внедрением общего TargetProfile и sandbox enforcement.

После этой синхронизации реализация универсального target слоя начинается в T02, а Project Discovery — в T03.
