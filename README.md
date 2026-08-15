# 🛡️ CFT Security Agent

> **AI-agent for contextual vulnerability prioritization and safe iterative verification inside a CI/CD pipeline.**

<p align="center">
  <strong>SAST → Context → CVSS → AI Agent → Validator → Executor → Evidence → Report</strong>
</p>

---

## 📌 What is this project?

This repository is a starter architecture for the NSU / CFT task:

**“AI agent for finding vulnerabilities in software inside a CI/CD pipeline.”**

The original task consists of two connected parts:

1. **Contextual vulnerability scoring**
   - take findings from static analysis;
   - understand where the vulnerable service lives in the architecture;
   - consider critical assets, trust zones, connections and exposure;
   - prioritize findings according to the real context of the target system.

2. **AI pentest agent**
   - analyze code and existing findings;
   - form a security hypothesis;
   - propose a safe verification action;
   - pass the action through a policy validator;
   - execute only approved checks inside local/sandbox/staging environments;
   - collect evidence;
   - re-evaluate the hypothesis;
   - stop with `confirmed`, `rejected`, `inconclusive` or `policy_blocked`.

The goal of the first milestone is **not** to build a full autonomous pentest platform.

The goal is to prove that **one finding can safely pass through the complete pipeline end-to-end**.

---

## ✨ Core idea

```text
Target repository
      │
      ▼
     SAST
      │
      ▼
   Finding
      │
      ├──────────────► Code context
      │
      └──────────────► Architecture context
                         │
                         ▼
                  CVSS + Context Priority
                         │
                         ▼
                      AI Agent
                         │
                         ▼
                     Hypothesis
                         │
                         ▼
                   ActionProposal
                         │
                         ▼
                      Validator
                  ┌──────┴──────┐
               APPROVE         DENY
                  │              │
                  ▼              ▼
               Executor     Policy blocked
                  │              │
                  ▼              │
               Evidence ◄────────┘
                  │
                  ▼
              Re-evaluate
             ┌────┴────┐
          continue     stop
             │          │
             └──────► Report
```

### The hard boundary

> **Agent thinks. Validator permits. Executor executes.**

The LLM never gets direct arbitrary shell access.

---

## 🧱 Repository structure

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
    └── test_contracts.py
```

---

## 🎯 MVP scope

The first working prototype should be able to:

- receive one normalized SAST finding;
- read the relevant code context;
- load architecture context;
- calculate or attach CVSS;
- calculate a separate Context Priority;
- form a hypothesis;
- create an `ActionProposal`;
- pass it through `Validator`;
- run **one predefined safe check** through `Executor`;
- collect structured evidence;
- classify the finding as `confirmed`, `rejected`, `inconclusive` or `policy_blocked`;
- produce a final report.

### Explicitly out of scope for MVP

- multi-agent orchestration;
- GNN-based architecture analysis;
- arbitrary code execution from the LLM;
- production pentesting;
- production CI/CD rollout;
- Kubernetes;
- large tool catalogs;
- vector databases;
- complex RAG pipelines;
- automatically generated executable exploit code.

---

## 🧠 Shared contracts

The project is intentionally **contract-first**.

Before everyone writes internal implementation, all modules agree on the same objects:

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

This prevents the common integration failure where every team member invents their own JSON shape.

---

## 🔎 Finding

A scanner-specific result is normalized into one common structure.

```json
{
  "id": "finding-001",
  "source": "semgrep",
  "rule_id": "python.django.security.example",
  "title": "Potential authorization issue",
  "description": "Requires review",
  "file": "backend/core/views.py",
  "line_start": 42,
  "line_end": 58,
  "severity": "WARNING",
  "service": "backend"
}
```

Later, CodeQL or another scanner can be added through another adapter without changing the Agent.

---

## 🗺️ Architecture context

For MVP, a plain YAML/JSON architecture description is enough.

No GNN is required.

Useful fields:

- service;
- public exposure;
- trust zone;
- service criticality;
- connected services;
- databases;
- paths to critical assets.

```yaml
services:
  frontend:
    type: frontend
    public: true
    connects_to:
      - backend

  backend:
    type: api
    criticality: high
    connects_to:
      - database
      - gigachat

  database:
    type: database
    criticality: critical
```

---

## 📊 CVSS and Context Priority

These are intentionally separated.

### CVSS

CVSS describes **technical severity**.

The score must be calculated deterministically by code/library from a vector.

The LLM may help propose justified metric values, but it must not invent a final score.

### Context Priority

Context Priority answers:

> “How important is this finding inside this specific system?”

Possible signals:

- public-facing service;
- critical asset;
- connection to a database;
- short path to a critical node;
- privileged trust zone;
- expected blast radius.

Example:

```text
CVSS: 7.4 HIGH
Context Priority: CRITICAL
Reason:
- public backend
- connected to customer database
- critical service path
```

---

## 🤖 Agent workflow

The intended orchestration model is LangGraph-like:

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
  ├─ no  → policy_blocked → report
  └─ yes → execute
             ↓
         collect_evidence
             ↓
          reevaluate
             ↓
           stop?
         ├─ no  → analyse
         └─ yes → report
```

The starter repository contains an implementation-neutral graph skeleton.
The real LangGraph dependency can be connected after the contracts stabilize.

---

## 🔐 Validator

The Validator is a **policy gate**.

It receives an `ActionProposal` and checks:

- is the target allowed?
- is the environment local/sandbox/staging?
- is the tool on the allowlist?
- are required parameters present?
- are limits respected?
- is the request inside scope?

Critical restrictions must be deterministic code/policy, not an LLM-only decision.

---

## ⚙️ Executor

The Executor is intentionally boring.

It:

1. accepts an `ActionProposal`;
2. requires an approved `ValidationResult`;
3. resolves the requested tool through a registry;
4. executes only predefined safe actions;
5. applies limits;
6. returns structured results;
7. stores evidence/audit information.

The Executor **does not plan**, **does not reinterpret the goal**, and **does not accept arbitrary commands**.

---

## 🧾 Evidence

Execution output is normalized into evidence:

```json
{
  "id": "evidence-001",
  "action_id": "action-001",
  "type": "test_result",
  "summary": "Controlled verification completed",
  "artifact_refs": [],
  "reliability": "high"
}
```

The Agent uses evidence to decide whether the finding is confirmed, rejected or inconclusive.

---

## 🧪 First target: SberLab

The old SberLab hackathon project is used as **Target v1**.

Important:

- do not intentionally plant vulnerabilities;
- do not mix the target code with the Agent code;
- keep it as a separate local repository;
- run checks only against local/test copies.

Recommended workspace:

```text
workspace/
├── cft-security-agent/
└── sberlab-target/
```

The target is referenced through `targets/sberlab.yaml`.

---

## 👥 Ownership

| Area | Owner |
|---|---|
| Core schemas / contracts | Stavr |
| AgentState / LangGraph workflow | Stavr |
| System prompt / integration | Stavr |
| CVSS / Context Priority | Lekha |
| Security tools / Validator / Evidence rules | Roma |
| Docker / Executor / sandbox / CI/CD integration | Kirill |

The ownership is about internal implementation. **Public interfaces stay shared and stable.**

---

## 🌿 Git workflow

Keep `main` runnable.

Recommended feature branches:

```text
feature/core-schemas
feature/agent-graph
feature/cvss
feature/context-priority
feature/validator
feature/executor
feature/sberlab-runtime
```

Prefer small PRs.

---

## 🚦 Development order

### Phase 1. Skeleton + contracts

Create repository structure, schemas, interfaces, default policy, target manifest, and stub Agent/Validator/Executor.

### Phase 2. Target runtime

Bring SberLab up reliably:

- persistent SQLite volume;
- one source of truth for startup command;
- `/health/`;
- reproducible dependencies.

### Phase 3. SAST input

```text
Semgrep raw JSON
      ↓
Normalizer
      ↓
Finding[]
```

### Phase 4. Scoring

```text
Finding
+
ArchitectureContext
      ↓
CVSS + Context Priority
```

### Phase 5. Agent with stubs

Run the whole workflow with fake Validator and fake Executor first.

### Phase 6. Real Validator

Replace the fake policy gate with deterministic rules.

### Phase 7. Safe Executor

Replace the fake executor with a registry of approved sandbox actions.

### Phase 8. End-to-end demo

```text
SberLab
  ↓
SAST
  ↓
Finding
  ↓
Context
  ↓
Scoring
  ↓
Agent
  ↓
Validator
  ↓
Executor
  ↓
Evidence
  ↓
Final Report
```

---

## ▶️ Quick start

Requirements:

- Python 3.11+
- optional local SberLab target

Create environment:

```bash
python -m venv .venv
pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

Run starter demo:

```bash
python -m app.main
```

The starter demo intentionally uses safe stubs. It exists to validate contracts and integration order.

---

## 🧩 Configuration

Copy `.env.example` to `.env` and adjust if needed.

```env
CFT_ENV=local
CFT_POLICY_FILE=policies/default.yaml
CFT_TARGET_FILE=targets/sberlab.yaml
CFT_MAX_ITERATIONS=5
```

---

## ✅ Definition of done for MVP

The MVP is complete when one finding can traverse the complete pipeline and produce a reproducible report:

```text
Finding
→ Code/Architecture Context
→ CVSS + Context Priority
→ Hypothesis
→ ActionProposal
→ Validator
→ Executor
→ Evidence
→ Re-evaluation
→ FinalReport
```

Everything else is secondary.

---

## 🛡️ Safety principles

1. Active checks only against explicitly allowed local/test targets.
2. Agent never executes actions directly.
3. Executor only runs predefined registered actions.
4. Validator approval is mandatory.
5. No arbitrary shell command path from LLM output.
6. Every execution produces an audit record.
7. Stop conditions and iteration limits are mandatory.
8. Evidence is required before declaring a finding confirmed.

---

## 📚 Project documentation

- [`AGENTS.md`](AGENTS.md): full context for AI coding agents and contributors.
- [`docs/architecture.md`](docs/architecture.md): system architecture and boundaries.
- [`docs/contracts.md`](docs/contracts.md): shared contracts between modules.
- [`docs/demo_case.md`](docs/demo_case.md): end-to-end demo definition.

---

<p align="center">
  <strong>Build one complete safe pipeline first. Scale only after it works.</strong>
</p>
