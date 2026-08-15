# Executor: внесённые дополнения и проверка с SberLab

Дата проверки: 15 августа 2026 года.

## 1. Цель работы

В репозитории `cft-security-agent` реализован минимальный безопасный Executor
для контролируемого выполнения действий пентест-агента в локальной тестовой
среде.

Работа разделялась на две задачи:

1. Создать предсказуемый Executor, который принимает только уже одобренный
   `ActionProposal` и выполняет исключительно заранее зарегистрированные
   действия.
2. Добавить защитную обвязку: timeout, ограничения ресурсов и числа запусков,
   отдельную рабочую директорию, сбор результатов, audit log и постоянное
   evidence.

Production-grade изоляция и доступ к production-системам в объём работы не
входили.

## 2. Реализованный поток выполнения

```text
ActionProposal
→ Validator
→ ApprovalRecord
→ проверка approval и хеша предложения
→ проверка target и окружения
→ выбор зарегистрированной capability
→ резервирование лимита запуска
→ запуск worker в отдельном sandbox-процессе
→ сбор stdout/stderr и exit code
→ сохранение JSON evidence
→ запись JSONL audit event
→ ExecutionResult
→ чтение evidence агентом
→ переоценка результата
```

Executor не планирует действия, не обращается к LLM и не интерпретирует
переданные параметры как команды.

## 3. Approval и защита ActionProposal

Добавлен `InMemoryApprovalStore`.

При одобрении сохраняются:

- `action_id`;
- причина одобрения;
- применённые policy rules;
- SHA-256 хеш полного `ActionProposal`.

Executor отклоняет запуск, если:

- approval отсутствует;
- `action_id` не совпадает;
- предложение было изменено после одобрения;
- target или окружение не разрешены;
- capability отсутствует в реестре;
- переданы недопустимые параметры.

## 4. Разрешённые capabilities

Зарегистрированы только три действия:

| Capability | Назначение |
|---|---|
| `safe_noop` | Детерминированная проверка LangGraph без воздействия на target |
| `check_sberlab_health` | Фиксированный `GET /health/` |
| `get_sberlab_public_projects` | Фиксированный `GET /api/projects/` |

HTTP-capabilities не принимают параметры из `ActionProposal`. Агент не может
изменить URL, HTTP-метод или путь запроса.

## 5. Sandbox и ограничения

Capability запускается отдельным процессом через фиксированный argv и
`shell=False`. Произвольная команда от агента в процесс не передаётся.

Для каждого запуска создаётся отдельная временная директория внутри:

```text
executor_data/workspaces/
```

После завершения или принудительной остановки процесса директория удаляется.

Лимиты по умолчанию:

| Ограничение | Значение |
|---|---:|
| Wall timeout | 5 секунд |
| CPU time | 2 секунды |
| Память процесса | 256 MiB |
| Максимальный размер файла | 1 MiB |
| Максимальное число процессов | 8 |
| Сохраняемый stdout/stderr | 16 KiB каждый |
| Запусков одного `action_id` | 1 |
| Одновременных запусков | 1 |
| Итераций проверки finding | 5 |

На Linux/WSL ограничения CPU, памяти, файлов и процессов применяются через
POSIX resource limits. На native Windows соответствующий тест пропускается;
wall timeout, ограничение вывода, отдельная директория, allowlist и запрет shell
остаются активными.

Timeout принудительно останавливает worker и возвращает структурированный
результат:

```text
status=failed
exit_code=124
timed_out=true
```

Исключение или ненулевой exit code также преобразуются в `ExecutionResult`, а
не выбрасываются наружу из узла Executor.

## 6. ExecutionResult, evidence и audit

`ExecutionResult` содержит:

```text
run_id
action_id
status
stdout
stderr
exit_code
timed_out
duration_ms
evidence_ref
audit_ref
artifacts
```

Результаты сохраняются отдельно от временной рабочей директории:

```text
executor_data/
├── evidence/
│   └── execution-<uuid>.json
├── audit/
│   └── executor.jsonl
└── workspaces/
```

Evidence содержит результат запуска, применённые лимиты, `run_id`,
`action_id`, stdout/stderr, exit code и ссылку на audit.

Audit log содержит отдельную JSONL-запись для каждого решения Executor:
время, capability, target, status, exit code, timeout, длительность и
`evidence_ref`.

На этапе `collect_evidence` агент заново открывает JSON по `evidence_ref` и
сверяет `run_id` и `action_id`. Успешный результат получает высокую надёжность
только после чтения сохранённого evidence.

## 7. Поведение pipeline при ошибках

Зависший или завершившийся с ошибкой запуск не ломает LangGraph pipeline.

- Результат ошибки сохраняется как evidence.
- Агент получает структурированный `ExecutionResult`.
- При наличии оставшихся итераций выполняется следующая одобренная проверка.
- Если последующая проверка успешна, workflow может завершиться со статусом
  `confirmed`.
- Если ошибки продолжаются до лимита, workflow завершается как
  `inconclusive`, а не падает с необработанным исключением.

## 8. Основные добавленные и изменённые файлы

```text
executor/approvals.py       # trusted approval store и хеш ActionProposal
executor/executor.py        # проверки и orchestration выполнения
executor/sandbox.py         # процесс, рабочая директория и лимиты
executor/worker.py          # фиксированные capabilities
executor/targets.py         # trusted target registry
evidence/store.py           # постоянное JSON evidence
evidence/audit.py           # JSONL audit log
schemas/execution.py        # расширенный ExecutionResult
agent/nodes.py              # запуск Executor и чтение evidence
app/config.py               # конфигурация лимитов и runtime-каталогов
app/executor_demo.py        # интеграционный health-запуск
policies/default.yaml       # разрешённые tools, environments и лимиты
tests/test_executor.py      # проверки Executor
tests/test_sandbox.py       # timeout, exit code и resource limits
tests/test_agent_graph.py   # восстановление pipeline после ошибки
docs/executor.md            # описание защитной границы
```

## 9. Среда интеграционной проверки

Проверка выполнялась на native Windows с Docker Desktop.

- SberLab был поднят отдельным Docker Compose проектом.
- Backend SberLab был опубликован на `127.0.0.1:8000`.
- Executor запускался локальным Windows Python из отдельного репозитория.
- Target: только локальный `sberlab-local`.
- Внешние и production-системы не использовались.

## 10. Проведённые проверки

### 10.1. Полный набор тестов

Команда:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -rs
```

Результат:

```text
30 passed, 1 skipped, 1 warning
```

Единственный skip:

```text
tests/test_sandbox.py: POSIX resource limits
```

Skip ожидаем на native Windows. Тест требует Linux/WSL. Код завершения pytest —
`0`.

### 10.2. Статический анализ

Результат Ruff:

```text
All checks passed!
```

### 10.3. Детерминированный LangGraph workflow

Команда:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

Результат:

```text
Workflow status: confirmed
```

### 10.4. Проверка SberLab health

Локальный endpoint:

```text
http://127.0.0.1:8000/health/
```

Результат:

```text
HTTP 200
{"status": "ok", "database": "ok"}
```

Это подтверждает доступность backend и успешную проверку базы данных.

### 10.5. Реальный запуск Executor против SberLab

Команда:

```powershell
.\.venv\Scripts\python.exe -m app.executor_demo
```

Полученный результат:

```text
status=completed
exit_code=0
timed_out=false
```

Идентификаторы запуска:

```text
run_id=3584e55c93d54d1582d20188420dc62c
evidence_ref=execution-132cc6e010f64a3b8cd40f714bc01245
```

Созданный evidence:

```text
executor_data/evidence/execution-132cc6e010f64a3b8cd40f714bc01245.json
```

Audit log:

```text
executor_data/audit/executor.jsonl
```

`run_id` и `evidence_ref` совпали между `ExecutionResult`, evidence и audit.

### 10.6. Очистка рабочей директории

После завершения запуска каталог:

```text
executor_data/workspaces/
```

не содержал рабочей директории завершённого процесса.

### 10.7. Timeout и ошибочный запуск

Целевые тесты защитной обвязки завершились результатом:

```text
12 passed, 1 skipped
```

Подтверждено:

- timeout возвращает `exit_code=124` и `timed_out=true`;
- один timeout не разрушает pipeline;
- после успешной следующей итерации pipeline получает `confirmed`;
- повторные ошибки до лимита завершают workflow как `inconclusive`.

### 10.8. Отсутствие произвольного выполнения команд

Статическая проверка каталогов `executor/`, `app/` и `agent/` подтвердила
отсутствие:

```text
shell=True
os.system
eval(
exec(
```

Worker запускается с фиксированным argv и `shell=False`. В реестре доступны
только три заранее определённые capabilities.

## 11. Итог

Обе задачи выполнены и подтверждены интеграционной проверкой с настоящим
локальным SberLab.

```text
EXECUTOR RESULT: PASS
```

Подтверждены:

- корректный approval-only запуск;
- запрет произвольных команд и URL;
- работа фиксированной health-capability;
- доступность SberLab и его базы данных;
- сбор stdout/stderr и exit code;
- сохранение и последующее чтение evidence;
- запись audit log;
- очистка отдельной рабочей директории;
- безопасная обработка timeout и ошибок;
- штатное продолжение и завершение pipeline.

Дополнительная функциональная доработка этих двух задач не требуется. Следующий
организационный шаг — перенести проверенную версию в Git-ветку, создать commit и
открыть Pull Request.
