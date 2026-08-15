# Contracts

The repository follows contract-first development.

| Contract | Producer | Consumer |
|---|---|---|
| Finding | SAST adapter | Agent / Scoring |
| ArchitectureContext | Architecture service | Scoring / Agent |
| CVSSResult | Scoring | Agent / Report |
| ContextPriority | Scoring | Agent / Report |
| Hypothesis | Agent | Agent workflow |
| ActionProposal | Agent | Validator |
| ValidationResult | Validator | Executor / Agent |
| ExecutionResult | Executor | Evidence normalizer |
| Evidence | Evidence layer | Agent / Report |
| FinalReport | Agent | CI/CD / human |

Do not duplicate these schemas in feature modules.
