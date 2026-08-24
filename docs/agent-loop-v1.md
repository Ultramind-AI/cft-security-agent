# Adaptive Agent Loop v1

T15 turns the previous bounded retry loop into an evidence-driven investigation loop.

```text
observe accumulated state/Evidence
        ↓
reason
        ↓
build fresh DynamicPlan
        ↓
validate scope + policy
        ↓
act inside sandbox
        ↓
normalize Evidence
        ↓
reevaluate
        ├─ terminal Evidence → stop
        ├─ budget exhausted → inconclusive
        └─ continue → reason again
```

## Adaptive behavior

Only the first action of the current `DynamicPlan` is executed. When it finishes,
its Evidence is appended to `AgentState`. The next reasoning iteration receives the
full accumulated Evidence and action/decision history and creates a new plan.

This guarantees that a second action can depend on the first observation instead of
blindly replaying a plan generated before that observation existed.

## AgentState memory

T15 keeps serializable run memory:

- optional `project_discovery`;
- current `TargetProfile` and `RuntimeServiceMap`;
- `plan_history`;
- `action_history` with validation/execution/evidence ids;
- `decision_history` with continue/stop reason;
- accumulated static/runtime `evidence`;
- `sandbox_session_id`;
- `iteration_count` / `max_steps`;
- `started_at` / `wall_clock_budget_seconds`;
- structured `stop_reason`.

No provider SDK objects or live runtime handles are stored in LangGraph state.

## Budgets and stop reasons

The loop is bounded by both step count and wall-clock time. Supported stop reasons are:

- `terminal_evidence`;
- `policy_blocked`;
- `plan_rejected`;
- `step_budget_exhausted`;
- `wall_clock_budget_exhausted`;
- `execution_timeout`;
- `build_failure`;
- `unsupported_runtime`;
- `isolation_or_policy_blocked`;
- `execution_failed`;
- `insufficient_evidence`.

A budget stop is `inconclusive`, not a security failure.

## Verdict boundary

T15 does not give the LLM authority to manufacture `confirmed` or `rejected`.
Capability-specific terminal Evidence still wins over model interpretation. Generic
`sandbox_command` output is an observation and has no vulnerability verdict by itself.
The deterministic Evidence Guard and CI Gate remain downstream authorities.
