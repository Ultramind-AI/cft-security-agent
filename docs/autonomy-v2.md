# Agent Autonomy v2

Autonomy v2 расширяет исследование внутри managed lab, но не расширяет права агента.

```text
deterministic inventory -> LLM interpretation -> verified claims
SAST + model scout -> CandidateFinding -> Validator -> Executor -> Evidence Guard -> Gate
```

## LLM routes

По умолчанию используются только NSU DeepCode: `deepseek-ai/DeepSeek-V4-Flash`, затем `Qwen3.8-27B`. Ключ берется из `NSU_OPENWEBUI_KEY`, effort - из `CFT_LLM_REASONING_EFFORT` (`low`, `high`, `max`, default `high`). Внешние fallback выключены, пока явно не включен `CFT_LLM_ALLOW_EXTERNAL_FALLBACKS=true`.

## Managed agent lab

На одну investigation создается один disposable Docker container. `/target` монтируется read-only, `/workspace` доступен на запись и сохраняется между action одной цепочки. Container подключается только к trusted target Docker network, без host network, docker socket, host mounts, CI secrets и внешнего интернета. Закрытие lab идет в `finally`, включая ошибку графа.

`sandbox_command` принимает только bounded argv и cwd `/target` или `/workspace`. Это не новый Executor: каждое действие проходит Validator, привязанный approval и registered capability.

## Facts и verdict

Discovery сначала строит inventory детерминированно. LLM может добавить только claim с `source_paths` из inventory и известными signal values, иначе результат отклоняется. Model scout может предложить CandidateFinding с source `model_scout`, но он попадает в тот же normal finding/evidence pipeline и сам не подтверждает уязвимость.

Evidence привязан к target, action, run, hypothesis, sandbox session и artifact refs. Evidence Guard и Gate остаются детерминированными: текст LLM не создает `confirmed`.
