# Security tools v0.2: reusable bounded verification capabilities

The security-tool layer is designed around **reusable verification classes**, not
"one finding = one tool".

The agent still cannot provide arbitrary shell commands, repository paths or URLs.
Instead, the operator-owned target config defines stable artifact ids. The Agent may
select an allowlisted capability, while application code reconstructs the exact trusted
parameters for that finding before Validator sees the `ActionProposal`.

## Current reusable capabilities

### `inspect_dockerfile_user`

Purpose: inspect the final build stage of a trusted Dockerfile artifact.

SberLab mappings:

```text
backend/Dockerfile           -> backend_dockerfile
frontend/frontend/Dockerfile -> frontend_dockerfile
```

Evidence distinguishes:

```text
missing USER        -> confirmed
explicit root USER  -> confirmed
explicit non-root   -> rejected
dynamic USER        -> inconclusive
```

The claim is source-only. The result always records:

```text
scope = source
runtime_user_verified = false
```

The same capability therefore covers findings 1 and 3 without duplicating Executor logic.

### `inspect_python_password_assignment`

Purpose: parse a trusted Python artifact with the standard-library AST and verify password
assignment/validation facts.

For the current SberLab seed finding it records:

```text
set_password call count
validate_password call count
hardcoded password literal count
privileged hardcoded-password record count
```

Password literal **values are never emitted into Evidence**. Only counts/booleans are kept.
The current result is source-only and does not claim that an authentication attempt was made:

```text
runtime_auth_verified = false
```

This capability confirms the Semgrep source condition when password assignment is present and
no `validate_password` call exists. If both assignment and validation calls exist but their
relationship is ambiguous, the result is `inconclusive` rather than guessed.

### `inspect_react_dangerous_html_flow`

Purpose: perform a bounded static source-flow check for a React dangerous HTML sink without
executing browser content.

For the current SberLab flow the trusted target profile binds:

```text
frontend App source
User model
User serializer
User viewset
field = about
```

The capability verifies facts such as:

```text
dangerouslySetInnerHTML sink exists
sink references the configured field
an obvious sanitizer is or is not present at the sink
model field exists
serializer exposes the field
serializer does not mark it read-only
ModelViewSet exposes an update route
whether authentication is required by the viewset
```

A terminal `confirmed` means the bounded **static source flow** from a writable field to the
raw HTML sink is established. It does not claim that browser-side execution was exercised:

```text
scope = static_source_flow
browser_execution_verified = false
```

No active browser payload is required for the MVP verification path.

## Finding -> verification strategy matrix

| SAST finding | Verification class | Capability |
|---|---|---|
| backend Dockerfile missing USER | container hardening / source config | `inspect_dockerfile_user` |
| unvalidated password assignment in demo seed | Python password handling / AST | `inspect_python_password_assignment` |
| frontend Dockerfile missing USER | container hardening / source config | `inspect_dockerfile_user` |
| React dangerous HTML sink | bounded static source flow | `inspect_react_dangerous_html_flow` |

So four findings currently map to **three reusable capability classes**.

## Trusted artifact registry

`targets/sberlab.yaml` owns the repository paths. An `ActionProposal` contains artifact ids,
not paths:

```text
Agent/LLM
-> tool + deterministic artifact id
-> Validator parameter contract
-> Executor
-> trusted target artifact registry
-> worker reads only the resolved artifact
```

The worker re-validates that every artifact path is relative and remains inside the trusted
repository root.

## Existing operational capabilities

These remain available for runtime/demo support but are not substitutes for vulnerability
Evidence:

```text
check_sberlab_health
get_sberlab_public_projects
safe_noop
```

`safe_noop` remains a deterministic test fixture only.

## Security invariant

```text
LLM chooses a capability class
Application fixes security-sensitive parameters
Validator approves the exact ActionProposal
Executor resolves trusted target artifacts
Worker performs one bounded verification
Evidence determines the verdict
```

There is still no arbitrary shell, arbitrary URL, arbitrary source path, or direct LLM ->
Executor execution path.

## Demo evidence scope output

`app.e2e_demo` prints the scope carried by every structured capability result.
This keeps a terminal `confirmed` precise about what was actually verified:

```text
Dockerfile check: Evidence scope: source-only (runtime_user_verified=False)
Password check:   Evidence scope: source-only (runtime_auth_verified=False)
React flow check: Evidence scope: static-source-flow (browser_execution_verified=False)
```

When the deterministic evidence guard already has a terminal capability verdict and
LLM tracing is enabled, the reevaluation short-circuit is also visible:

```text
[agent] reevaluate: deterministic evidence guard -> confirmed
```

The absence of an LLM `reevaluate` request in that case is intentional: structured
Evidence remains authoritative, and the LLM is only used for reevaluation when the
evidence is non-terminal.
