# 🛡️ CFT Security Agent

> AI-agent for contextual vulnerability prioritization and safe iterative verification inside a CI/CD pipeline.

<p align="center">
  <strong>SAST → Context → CVSS → AI Agent → Validator → Executor → Evidence → Report</strong>
</p>

---

## 📌 О проекте

Репозиторий содержит базовую архитектуру решения для задачи ЦФТ:

**«ИИ-агент для поиска уязвимостей в ПО в CI/CD-контуре»**

Проект состоит из двух связанных частей:

1. Контекстный скоринг уязвимостей
2. ИИ-агент-пентестер с безопасной итеративной проверкой гипотез

Цель MVP:

```text
один finding
→ контекст
→ scoring
→ hypothesis
→ ActionProposal
→ Validator
→ Executor
→ Evidence
→ FinalReport
```

---

## 🧠 Главный принцип

```text
Agent думает
Validator разрешает
Executor выполняет
```

LLM не получает прямой произвольный shell-доступ.

---

## 🧱 Структура проекта

```text
cft-security-agent/
│
├── README.md
├── AGENTS.md
├── pyproject.toml
├── .env.example
├── .gitignore
│
├── app/
│   ├── config.py
│   └── main.py
│
├── schemas/
│   ├── finding.py
│   ├── architecture.py
│   ├── scoring.py
│   ├── hypothesis.py
│   ├── action.py
│   ├── validation.py
│   ├── execution.py
│   ├── evidence.py
│   ├── report.py
│   └── state.py
│
├── agent/
│   ├── graph.py
│   ├── nodes.py
│   └── prompts.py
│
├── tools/
│   ├── contracts.py
│   └── registry.py
│
├── scoring/
│   └── service.py
│
├── validator/
│   └── validator.py
│
├── executor/
│   └── executor.py
│
├── evidence/
│   └── store.py
│
├── sast/
│   └── normalizer.py
│
├── architecture/
│   └── service.py
│
├── policies/
│   └── default.yaml
│
├── targets/
│   ├── sberlab.yaml
│   └── sberlab_architecture.yaml
│
├── docs/
│   ├── architecture.md
│   ├── contracts.md
│   └── demo_case.md
│
└── tests/
    ├── test_contracts.py
    └── test_agent_graph.py
```

---

## 🚀 Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
```

Ожидаемый результат:

```text
6 passed
```

Ручной тест workflow:

```bash
python3 -m app.main
```

Ожидаемо:

```text
Workflow status: confirmed
Finding: demo-001
Iterations: 1
Evidence count: 1
```

---

## 🔄 Текущий LangGraph workflow

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
  ├─ no  → report → END
  └─ yes
       ↓
    execute
       ↓
 collect_evidence
       ↓
  reevaluate
       ↓
     stop?
   ├─ yes → report → END
   └─ no  → analyse
```

---

## 🧪 Что сейчас настоящее, а что stub

Настоящее:

- shared schemas;
- AgentState;
- LangGraph orchestration;
- conditional edges;
- iteration loop;
- Validator policy gate;
- safe Executor registry;
- Evidence;
- FinalReport;
- tests.

Пока заглушки:

- CVSS;
- Context Priority;
- реальный анализ кода через LLM;
- реальные security checks;
- реальная интеграция с SberLab runtime.

---

## 👥 Распределение зон

| Зона | Ответственный |
|---|---|
| AgentState / LangGraph / интеграция | Ставр |
| CVSS / Context Priority | Лёха |
| Validator / Evidence / security tools | Рома |
| Docker / Executor / runtime / CI/CD | Кирилл |

---

## 🛡️ Ограничения MVP

Не делаем пока:

- multi-agent;
- GNN;
- production CI/CD;
- Kubernetes;
- произвольный shell от LLM;
- большой каталог pentest tools;
- сложный RAG;
- production pentesting.

---

## ✅ Definition of Done

MVP считается собранным, когда один реальный finding из SberLab проходит:

```text
SAST
→ Finding
→ Code Context
→ Architecture Context
→ CVSS
→ Context Priority
→ Hypothesis
→ ActionProposal
→ Validator
→ Executor
→ Evidence
→ FinalReport
```
