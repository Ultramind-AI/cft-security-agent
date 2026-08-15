# CFT Security Agent: AgentState + LangGraph patch v0.1

Это патч поверх уже существующего `cft-security-agent`.

## Что делает патч

Добавляет:

- реальный `AgentState`;
- настоящий `StateGraph` на LangGraph;
- узлы workflow;
- ветку APPROVE;
- ветку DENY;
- цикл при `inconclusive`;
- лимит итераций;
- тестовый `Finding`;
- fake scoring;
- существующие безопасные Validator + Executor;
- Evidence;
- FinalReport;
- system prompt v0.1;
- интеграционные тесты.

Пока всё активное выполнение остается тестовым и детерминированным.
Реальные компоненты Лёхи, Ромы и Кирилла позже заменят соответствующие заглушки,
не меняя общую структуру графа.

## Как применить

1. Распакуй ZIP куда угодно.
2. В терминале перейди в корень своего `cft-security-agent`.
3. Выполни:

```bash
python3 /ПУТЬ/ДО/cft-agent-graph-patch/apply_patch.py
```

Либо просто скопируй содержимое папки патча в корень репозитория с заменой файлов.

## Установка зависимостей

В уже активированном `.venv`:

```bash
python3 -m pip install -e ".[dev]"
```

## Главная проверка

```bash
python3 -m pytest -q
```

Ожидается:

```text
6 passed
```

Тесты проверяют:

1. старый безопасный `safe_noop`;
2. блокировку неизвестного tool;
3. путь `confirmed`;
4. путь `rejected`;
5. путь `policy_blocked`;
6. цикл `inconclusive` до лимита итераций.

## Ручной demo-прогон

```bash
python3 -m app.main
```

Ожидается примерно:

```text
Workflow status: confirmed
Finding: demo-001
Iterations: 1
Evidence count: 1
```

## Если всё зеленое

```bash
git add .
git commit -m "Добавлен базовый workflow агента на LangGraph"
git push
```
