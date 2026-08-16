# FinalReport v1.0

`FinalReport` is the stable boundary between the agent workflow and external consumers such
as CI/CD jobs, a future UI, audit storage or an API.

The report deliberately separates four questions:

1. **What finding was assessed?**
2. **What did the Agent/Validator/Executor actually do?**
3. **What Evidence supports the verdict, and what was not verified?**
4. **What should happen next?**

The JSON schema includes:

```text
schema_version
finding
status
analysis_summary / risk_signals / hypothesis
verification
cvss / context_priority
evidence
explanation
limitations
next_step
iterations
```

`verification.decision_basis` is explicit:

```text
capability_specific_evidence
validator_policy
iteration_limit
workflow_state
```

A `confirmed` or `rejected` security-tool result should therefore be traceable to structured
capability Evidence rather than execution success or an LLM conclusion. The terminal status refers
to the reported security condition within the recorded Evidence scope. It must not silently promote
a stronger LLM hypothesis, such as a source-only Dockerfile check into a claim about runtime UID.

## Human-readable demo output

`python -m app.e2e_demo ...` renders the same model as a compact report with sections for the
finding, Agent assessment, verification, Evidence, risk, limitations and next step.

## Machine-readable artifact

Use `--report-json` when another system needs the report:

```bash
python -m app.e2e_demo \
  --findings artifacts/sast/findings.json \
  --target ../sberlab_hack \
  --architecture targets/sberlab_architecture.yaml \
  --index 0 \
  --report-json artifacts/reports/finding-0.json
```

This is intentionally UI-neutral. A frontend can consume the JSON later without changing the
agent graph or Evidence semantics.
