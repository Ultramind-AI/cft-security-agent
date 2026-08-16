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
| ExecutionResult | Executor | Evidence |
| Evidence | Evidence layer | Agent / Report |
| FinalReport | Agent | CI/CD / human |

`ApprovalRecord` binds `action_id` to a SHA-256 digest of the complete
`ActionProposal`. Executor receives only the proposal and verifies that trusted
record before resolving a capability.

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
