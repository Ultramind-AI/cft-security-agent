# Demo Case v0

## Goal
Prove that one finding can safely traverse the whole system.

## Expected flow

```text
1. Normalize one SAST finding
2. Read code context
3. Load architecture context
4. Calculate CVSS
5. Calculate Context Priority
6. Form a hypothesis
7. Create ActionProposal
8. Validator approves/denies
9. Executor runs one predefined safe check
10. Evidence is stored
11. Agent re-evaluates
12. FinalReport is produced
```

## Success criteria

- every step uses shared schemas;
- no component bypasses Validator;
- Executor never receives arbitrary shell text;
- final status is reproducible;
- every active step targets only the configured local test target.
