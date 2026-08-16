# Validator policy v0.1

`Validator` is the deterministic policy gate between the reasoning agent and the
Executor.

The agent may propose an action, but it cannot grant itself permission. An action
reaches the Executor only after `ValidationResult.approved == true` and a trusted
approval record is created.

## Input

`ActionProposal` contains:

- `id` - stable action identifier;
- `tool` - registered capability name;
- `target` - trusted target id;
- `environment` - requested execution environment;
- `iteration` - finding verification iteration number;
- `parameters` - structured parameters only;
- `purpose` - why the action is needed;
- `expected_evidence` - what evidence the action is expected to produce.

## Deterministic checks

Validator v0.1 checks, in order:

1. supported policy version;
2. safe action id format;
3. target allowlist;
4. environment allowlist;
5. target/environment consistency when target metadata is loaded;
6. tool allowlist;
7. iteration limit;
8. non-empty purpose;
9. non-empty expected evidence;
10. per-tool allowed parameter names;
11. per-tool parameter values and string length limits;
12. mandatory audit logging policy.

The current policy lives in `policies/default.yaml`.

## Output

`ValidationResult` contains:

- `approved`;
- `action_id`;
- human-readable `reason`;
- `policy_rules`, including the passed rules and the blocking rule on DENY.

## Security boundary

The Validator is not an LLM. It does not interpret free-form commands and does not
execute anything. The Executor independently re-checks trusted approval, target,
environment, capability registration and proposal integrity. This is intentional
defense in depth.
