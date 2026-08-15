# Contracts

| Contract | Producer | Consumer |
|---|---|---|
| Finding | SAST adapter | Agent / Scoring |
| ArchitectureContext | Architecture service | Scoring / Agent |
| CVSSResult | Scoring | Agent / Report |
| ContextPriority | Scoring | Agent / Report |
| Hypothesis | Agent | Workflow |
| ActionProposal | Agent | Validator |
| ValidationResult | Validator | Executor / Agent |
| ExecutionResult | Executor | Evidence |
| Evidence | Evidence layer | Agent / Report |
| FinalReport | Agent | CI/CD / human |
