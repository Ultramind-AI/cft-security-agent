# DynamicPlan v1

T14 defines the structured planning boundary between hypothesis formation and the
existing `Validator -> Executor` path. T14.1 keeps that contract but removes the
old requirement that every concrete investigation step must already exist as a
precomputed candidate.

```text
Finding + accumulated Evidence
        ↓
LLM reasoning
        ↓
DynamicPlan
        ↓
DynamicPlanValidator (scope/budget)
        ↓
ActionProposal
        ↓
PolicyValidator
        ↓
Sandbox / deterministic capability
```

## Contract

`DynamicPlan` contains:

- one verification goal;
- the current `hypothesis_id`;
- a bounded `max_steps` budget;
- optional current `sandbox_session_id`;
- explicit stop conditions and continuation reason;
- ordered `PlannedAction` entries;
- an expected observation and continue condition for every step.

Each `PlannedAction` contains the existing canonical `ActionProposal`. DynamicPlan
therefore does not create a second execution contract.

## T14.1: freedom inside the lab

The reasoning model has two ways to propose a step:

1. choose a deterministic registered candidate, for example an allowlisted HTTP
   observation from `RuntimeServiceMap`;
2. propose `sandbox_command` as bounded `argv` executed inside a disposable Docker
   lab.

`sandbox_command` intentionally allows broad repository inspection and test commands.
The command is not executed on the host and does not inherit the target Compose
network. Its boundary is enforced by code:

- Docker is mandatory; there is no ProcessSandbox fallback;
- target repository is mounted read-only at `/target`;
- writable state is limited to ephemeral `/workspace`;
- root filesystem is read-only;
- capabilities are dropped and `no-new-privileges` is enabled;
- process, CPU, memory, output and wall-clock limits remain active;
- arbitrary network is disabled (`--network none`);
- target HTTP observations still go through registered `RuntimeServiceMap` endpoints;
- every `ActionProposal` still passes `DynamicPlanValidator` and `PolicyValidator`;
- stdout/stderr become bounded, redacted Evidence.

The LLM still cannot invent target identity, environment, sandbox session id or a
new network target. It can choose *what to do inside the disposable lab*, while code
owns the boundary of that lab.

## Scope validation

`DynamicPlanValidator` verifies that:

- target/environment match the current `TargetProfile`;
- hypothesis matches the current workflow state;
- the plan fits the remaining step budget;
- sandbox session matches the current `RuntimeServiceMap` when runtime scope is used;
- registered services/endpoints are present and ready;
- generic sandbox commands do not request service/endpoint network scope;
- action iterations follow plan order.

This validator is not execution permission. Every executed action still passes the
existing deterministic `PolicyValidator` immediately before `Executor`.

## T14 vs T15

T14/T14.1 defines *what a valid plan/action looks like*. T15 owns the adaptive loop:
after every Evidence record the model reasons again and builds a fresh plan for the
next step. A multi-step plan is useful as intent, but the graph intentionally executes
one step at a time so step N+1 can change after observing step N.
