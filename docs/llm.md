# LLM fallback adapter v0.1

The live reasoning mode implements the existing `AgentReasoningModel` boundary.
It does not add a new execution path.

```text
Finding + bounded context + scoring
-> FallbackLLMAgentModel
-> Pydantic validation
-> ActionProposal
-> Validator
-> Executor
-> Evidence
-> guarded re-evaluation
```

## Default route order

По умолчанию маршрут остается внутри NSU DeepCode. Внешние провайдеры доступны
только при явном `CFT_LLM_ALLOW_EXTERNAL_FALLBACKS=true`.

1. NSU / `deepseek-ai/DeepSeek-V4-Flash`
2. NSU / `Qwen3.8-27B`

`NSU_OPENWEBUI_KEY` хранит ключ, а `CFT_LLM_REASONING_EFFORT=low|high|max`
управляет reasoning effort, по умолчанию `high`.

Override the chain with `CFT_LLM_ROUTES`, for example:

```text
CFT_LLM_ROUTES=groq:openai/gpt-oss-120b,mistral:mistral-large-latest
```


Provider keys intentionally keep their native names:

```text
GROQ_API_KEY=...
MISTRAL_API_KEY=...
OPENROUTER_API_KEY=...
```

They do not need a `CFT_` prefix. `Settings` stores them as `SecretStr` and passes
an explicit credential map into the LLM transport. The transport does not depend
on `os.getenv()` seeing values parsed from `.env`.

## Failure handling

Fallback occurs on network errors, unavailable models, invalid JSON and Pydantic
schema validation failures. `429`, authentication/access errors and payment errors
block the rest of that provider for the current request and move to another
provider.

## Security properties

- API keys are loaded by `Settings` from provider-native variables in environment or a local `.env`.
- `.env` is ignored by Git.
- No key is included in prompts, logs or errors.
- The LLM does not control target, environment, action id or iteration.
- The LLM can choose only from the small typed execution-tool set exposed by the
  application for the current finding.
- Validator remains mandatory for every active action.
- Provider-attempt error messages use the shared public-error redactor, so API
  keys, bearer credentials and common secret assignments are not retained in
  `LLMAttempt.error`.
- `confirmed` / `rejected` requires matching capability-specific Evidence.
- Raw execution success alone never becomes a vulnerability verdict.

## Data boundary

Live API mode sends the bounded finding context supplied by AgentState to the
selected external provider. Use it only for targets whose code may be sent to that
provider. For customer deployments, point the same adapter at the approved
customer-hosted compatible endpoint or replace the transport while preserving the
`AgentReasoningModel` contract.
