# AGENTS.md

## Purpose

This file is the canonical project context for AI coding agents and human contributors working on **cft-security-agent**.

Read it before changing architecture, schemas, Agent workflow, Validator, Executor, scoring or target integration.

Do not treat this repository as a generic pentest framework. It has a specific defensive CI/CD goal and deliberately constrained architecture.

---

# 1. Project context

The project is based on a CFT task: **AI agent for finding vulnerabilities in software inside a CI/CD pipeline.**

The task contains two connected subproblems, where the second includes the first.

## Part 1: contextual scoring

Inputs:

- architecture graph;
- static analysis findings;
- code/service metadata.

Goal:

Assign vulnerability severity and priority while considering where the affected service sits in the real system.

Important architecture signals include:

- public exposure;
- trust zone;
- service criticality;
- affected components;
- database connections;
- connectivity to critical nodes;
- possible blast radius.

The same technical vulnerability may have very different practical priority in two different services.

## Part 2: AI pentest agent

The agent additionally analyzes source code and iteratively verifies security hypotheses.

Intended loop:

```text
context
→ analysis
→ hypothesis
→ verification proposal
→ validator
→ approved execution
→ evidence
→ re-evaluation
→ stop or repeat
```

This is a defensive workflow for controlled CI/CD, local, sandbox and staging environments.

---

# 2. Core architecture decision

The central separation of responsibility is:

```text
Agent thinks
Validator permits
Executor executes
```

This is a hard boundary.

## Agent

The Agent:

- reads findings;
- reads code context;
- reads architecture context;
- reasons about vulnerability hypotheses;
- proposes verification actions;
- consumes evidence;
- decides whether more evidence is needed;
- produces the final report.

The Agent must not directly execute arbitrary actions.

## Validator

The Validator:

- receives an `ActionProposal`;
- checks deterministic policy;
- validates target and environment;
- checks tool allowlist;
- checks parameter constraints;
- checks limits;
- returns `APPROVE` or `DENY`.

Critical policy must be code/config based.

Do not use an LLM as the only safety gate.

## Executor

The Executor:

- receives a previously approved action;
- confirms approval;
- resolves an allowed tool from a registry;
- executes it only in an approved environment;
- applies limits;
- captures result;
- returns structured execution output and evidence references.

The Executor does not plan or reinterpret goals.

---

# 3. MVP philosophy

Do not attempt to build a complete autonomous pentest system first.

The first milestone is one end-to-end finding.

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
→ Re-evaluation
→ FinalReport
```

If one finding can safely and reproducibly traverse that pipeline, the architecture is proven.

Only then add more scanners, tools, findings, CI/CD integrations or advanced models.

---

# 4. Current target

The first target is the old SberLab hackathon repository.

Treat it as `Target v1`.

Do not intentionally insert vulnerabilities into it.

Do not build the Agent inside the SberLab Django project.

Recommended local layout:

```text
workspace/
├── cft-security-agent/
└── sberlab-target/
```

The Agent repository references the target through `targets/sberlab.yaml`.

All active verification must remain inside explicitly allowed local/test environments.

Never assume production authorization.

---

# 5. Repository ownership

## Stavr

Owns:

- project skeleton;
- schemas/contracts;
- AgentState;
- orchestration;
- system prompt;
- LangChain/LangGraph integration;
- tool interfaces;
- end-to-end integration.

## Lekha

Owns:

- CVSS 4.0 understanding and implementation;
- Context Priority;
- sample scoring of real SAST findings.

## Roma

Owns:

- security capability requirements;
- Validator rules;
- permission policy semantics;
- evidence criteria;
- required safe tool set.

## Kirill

Owns:

- SberLab runtime;
- Docker reliability;
- sandbox/runtime execution;
- Executor internals;
- time/resource limits;
- logging;
- CI/CD integration sketch.

Ownership does not allow changing shared contracts silently.

If a schema needs to change, treat that as an architecture change.

---

# 6. Contract-first design

All components communicate through typed shared schemas.

Do not pass unstructured dictionaries between major system boundaries unless a field is explicitly designed as free-form.

Canonical objects:

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

These live in `schemas/`.

When adding or changing a field:

1. explain why the field is needed;
2. update the schema;
3. update tests;
4. update every affected producer/consumer;
5. update documentation if the boundary changed.

Do not create local shadow versions of these models.

---

# 7. Finding contract

A `Finding` represents one normalized scanner result.

The source may eventually be Semgrep, CodeQL or another SAST engine.

The Agent must not depend on raw scanner JSON.

Scanner adapters normalize into the common model.

Useful fields:

- id;
- source;
- rule id;
- title;
- description;
- file path;
- line range;
- scanner severity;
- affected service.

---

# 8. ArchitectureContext contract

`ArchitectureContext` describes the affected service in system context.

For MVP it can be generated from a simple YAML/JSON graph.

Useful fields:

- service name;
- public exposure;
- criticality;
- trust zone;
- connected services;
- database connections;
- critical paths.

Do not introduce GNNs for MVP.

A simple graph library or deterministic traversal is enough.

---

# 9. Scoring model

There are two outputs.

## CVSSResult

Purpose: technical severity.

Requirements:

- preserve vector;
- preserve numeric score;
- preserve severity label;
- preserve reasoning/source of metric assumptions.

The numeric score should be deterministic.

Do not allow the LLM to simply invent a final number.

## ContextPriority

Purpose: deployment/system-specific importance.

Useful signals:

- public exposure;
- critical service;
- database connectivity;
- privileged environment;
- path to critical assets;
- blast radius.

Context Priority is not the same thing as CVSS.

Do not force every architecture feature into CVSS.

---

# 10. AgentState

`AgentState` is the shared workflow state.

Typical fields:

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
final_report
```

Every Agent node should have a narrow responsibility.

Examples:

```text
load_context
score_finding
analyse
form_hypothesis
propose_action
collect_evidence
reevaluate
report
```

A node should clearly document what it reads, what it writes, and possible state transitions.

---

# 11. Orchestration

Recommended implementation:

- LangChain for model interfaces, tool definitions and structured outputs;
- LangGraph for stateful cyclic orchestration.

Conceptual graph:

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
  ├─ no → policy_blocked → report → END
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

Use explicit stop conditions and always enforce a maximum iteration count.

---

# 12. Hypothesis

The Agent should not jump directly from finding to execution.

First create a structured hypothesis containing:

- statement;
- evidence it is based on;
- expected confirming/rejecting evidence;
- confidence.

The hypothesis is a reasoning artifact, not proof.

Only evidence can confirm or reject it.

---

# 13. ActionProposal

`ActionProposal` is the only way the Agent requests active verification.

It should contain:

- action id;
- tool name;
- target id;
- parameters;
- purpose;
- expected evidence.

Do not allow the Agent to send raw arbitrary shell commands through `ActionProposal`.

Tools must be named registered capabilities.

---

# 14. Tool architecture

A tool is a typed capability, not merely a binary/program name.

Each tool needs:

- stable name;
- purpose;
- input schema;
- output schema;
- permission level;
- side-effect classification;
- environment restrictions;
- documented errors.

Start with only the minimum tools needed for the first demo case.

Do not build a large catalog before the first end-to-end pipeline works.

---

# 15. Validator requirements

Validator should be deterministic wherever possible.

Check at least:

- target is allowlisted;
- environment is allowed;
- tool is allowlisted;
- required parameters exist;
- parameter values are inside allowed bounds;
- action is in scope;
- iteration and runtime limits are respected;
- audit logging is enabled.

Return a structured `ValidationResult` with a clear reason.

Denied actions still belong in the final report as `policy_blocked` or supporting evidence.

---

# 16. Permission policy

Default policy lives in `policies/default.yaml`.

The policy should describe:

- allowed targets;
- allowed environments;
- allowed tools;
- timeouts;
- iteration limits;
- logging requirements.

Do not hide security-critical rules inside prompts.

Prompts can instruct behavior. Policy and code enforce permissions.

---

# 17. Executor requirements

Executor should be intentionally simple.

It must:

- require approved validation;
- resolve tool through a local registry;
- reject unknown tools;
- reject mismatched action ids;
- enforce timeout;
- capture stdout/stderr where relevant;
- return an `ExecutionResult`;
- never accept arbitrary LLM-generated shell text.

For MVP, using safe stubs is preferred before real execution is connected.

---

# 18. Evidence model

Execution output is not automatically trustworthy evidence.

Normalize it.

Evidence should record:

- evidence id;
- action id;
- evidence type;
- summary;
- artifact references;
- reliability.

The Agent uses Evidence to decide:

```text
confirmed
rejected
inconclusive
```

The Agent must not mark a finding confirmed without evidence.

---

# 19. FinalReport

Final report should include:

- finding id;
- final status;
- CVSS;
- Context Priority;
- evidence references;
- short explanation;
- iteration count.

Allowed statuses:

```text
confirmed
rejected
inconclusive
policy_blocked
```

Avoid vague output statuses.

---

# 20. SAST architecture

Do not couple Agent code to Semgrep.

Use adapters:

```text
Semgrep JSON
   ↓
Semgrep adapter
   ↓
Finding[]
```

Later:

```text
CodeQL output
   ↓
CodeQL adapter
   ↓
Finding[]
```

Agent code remains unchanged.

---

# 21. Architecture graph implementation

MVP does not require ML.

Use YAML/JSON plus deterministic graph queries.

Examples of queries:

- neighbors of service;
- public exposure;
- path to critical asset;
- connected databases;
- trust zone.

Only add advanced graph embeddings/GNN if later evidence shows they are needed.

---

# 22. Development order

Follow this order unless there is a strong reason not to.

## Step 1

Create repository skeleton and shared schemas.

## Step 2

Make SberLab reproducible locally.

Expected runtime fixes include:

- persistent SQLite volume;
- a single startup command source;
- health endpoint;
- pinned dependency versions where practical.

## Step 3

Run first SAST scan and normalize findings.

## Step 4

Implement CVSS and Context Priority.

## Step 5

Build Agent flow using fake Validator and fake Executor.

## Step 6

Replace fake Validator with deterministic policy.

## Step 7

Replace fake Executor with safe registered actions.

## Step 8

Select one finding and run the complete demo.

Do not wait until every subsystem is “fully finished” before integration.

Integrate continuously.

---

# 23. Git rules

`main` should stay runnable.

Use feature branches by functionality.

```text
feature/core-schemas
feature/agent-graph
feature/cvss
feature/context-priority
feature/validator
feature/executor
feature/sberlab-runtime
```

Prefer small pull requests.

Do not mix unrelated architecture changes in one PR.

---

# 24. Coding style

General expectations:

- Python 3.11+;
- type hints;
- Pydantic models for boundaries;
- narrow functions;
- explicit errors;
- deterministic logic for policy;
- unit tests for schemas/Validator;
- integration tests for end-to-end workflow;
- no hidden global mutable state.

When a module boundary can be expressed as a typed model, prefer that over an unstructured dictionary.

---

# 25. Safety constraints

This project is defensive.

AI agents working in this repository must not introduce:

- arbitrary shell execution from model output;
- implicit production targets;
- credential harvesting logic;
- evasion features;
- destructive actions;
- hidden execution paths;
- bypasses around Validator;
- active checks against third-party systems.

All active checks must be limited to explicitly configured local/test targets.

If a requested change weakens the Agent → Validator → Executor boundary, treat it as a design bug unless explicitly reviewed by the team.

---

# 26. What NOT to build yet

Do not spend time on:

```text
GNN
multi-agent architecture
production Kubernetes
production CI/CD deployment
web UI
vector database
complex RAG
large scanner matrix
large execution-tool catalog
autonomous arbitrary code generation
```

These may be future work. They are not required to prove the architecture.

---

# 27. Definition of done

The first architecture milestone is done when:

1. one SAST finding is normalized;
2. code context is available;
3. architecture context is available;
4. CVSS exists;
5. Context Priority exists;
6. Agent forms a hypothesis;
7. Agent creates an ActionProposal;
8. Validator approves or denies it;
9. approved action runs through Executor;
10. Evidence is created;
11. Agent re-evaluates;
12. FinalReport is produced.

If this path is reproducible, we have a real prototype.

---

# 28. Rule for future AI agents

Before changing code, answer these questions internally:

1. Which component owns this responsibility?
2. Which shared contract is involved?
3. Does this bypass Validator?
4. Does this give the Agent direct execution power?
5. Can this be implemented deterministically instead of with an LLM?
6. Does this help the current end-to-end MVP?
7. Will the change break another contributor's interface?

If the answer indicates architecture drift, fix the design first.
