# Contracts

| Contract | Producer | Consumer |
|---|---|---|
| Finding | SAST adapter | Agent / Scoring |
| ArchitectureContext | Architecture service | Scoring / Agent |
| CVSSResult | Scoring | Agent / Report |
| ContextPriority | Scoring | Agent / Report |
| Hypothesis | Agent | Workflow |
| ActionProposal | Agent | Validator |
| ValidationResult | Validator | Approval store / Agent |
| ApprovalRecord | Trusted workflow boundary | Executor |
| ErrorDetail | Pipeline boundary | CI/CD / human |
| ExecutionResult | Executor | Evidence |
| Evidence | Evidence layer | Agent / Report |
| FinalReport | Agent | CI/CD / human |

`ApprovalRecord` binds `action_id` to a SHA-256 digest of the complete
`ActionProposal`. Executor receives only the proposal and verifies that trusted
record before resolving a capability.

`ErrorDetail` is the machine-readable system-error contract. It contains
`code`, `layer`, a public `message` and `retryable`. Security outcomes such as
Validator denial or `policy_blocked` are not system errors.

`GateResult.errors` is the canonical structured list. The legacy
`stage_errors: list[str]` field remains available and is derived from the public
messages in `errors`. Any mandatory structured error keeps the gate fail-closed
with `decision="fail"` and `exit_code=2`.

`ExecutionResult` always contains:

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

`evidence_ref` is resolved by `JsonExecutionEvidenceStore`; the agent reads the
persisted record and checks its `run_id` and `action_id` before using it.

`Evidence` may additionally contain a capability-specific deterministic `verdict`
(`confirmed`, `rejected`, or `inconclusive`) plus structured `details`. A verdict is
accepted only after the persisted Executor output has been parsed against the matching
capability schema. Generic execution records remain verdict-free.

## Tool contracts v0.1

The agent-facing tool catalog is defined in `tools/contracts.py`. The catalog is
metadata only: it fixes names, typed input/output schemas, expected errors and
permissions without granting the model any direct execution primitive. These
agent-facing contracts are deliberately separate from Executor capabilities such
as `safe_noop`, `check_sberlab_health`, or the fixed
`inspect_dockerfile_user`, `inspect_python_password_assignment` and `inspect_react_dangerous_html_flow` bounded source verifiers.

| Tool | Access | Purpose | Permission | Validator |
|---|---|---|---|---|
| `read_finding` | read-only | Load one normalized SAST finding | `finding:read` | no |
| `read_code_context` | read-only | Read a bounded source-code window | `code:read` | no |
| `get_architecture_context` | read-only | Load context for one service | `architecture:read` | no |
| `calculate_cvss` | scoring | Calculate CVSS 4.0 from explicit metrics | `scoring:calculate` | no |
| `calculate_context_priority` | scoring | Calculate architecture-aware priority | `architecture:read`, `scoring:calculate` | no |
| `request_verification` | execution request | Submit an `ActionProposal` for policy review | `verification:request` | yes |
| `read_evidence` | read-only | Load persisted Executor evidence | `evidence:read` | no |

`request_verification` is intentionally not an executor tool. It returns a
structured request that must cross the deterministic Validator boundary before
Executor can run a registered capability. The catalog contains no
`execution:direct` permission.

For CVSS, the contract accepts explicit metric values and documents that the
scoring implementation must not invent missing metrics. This keeps metric
selection/reasoning separate from the deterministic score calculation.

The contracts can be inspected as machine-readable JSON Schema:

```bash
python -m app.tool_contracts
python -m app.tool_contracts --name request_verification
```

A future LangChain adapter can build structured tools from these schemas without
changing the shared project models or the Validator/Executor boundary.
