from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from security.error_redaction import redact_error_message

TModel = TypeVar("TModel", bound=BaseModel)


DEFAULT_ROUTE_SPECS: tuple[str, ...] = (
    "nsu:deepseek-ai/DeepSeek-V4-Flash",
    "nsu:Qwen3.8-27B",
    "groq:openai/gpt-oss-120b",
    "mistral:mistral-large-latest",
    "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
    "cloudflare:@cf/openai/gpt-oss-120b",
    "nvidia:openai/gpt-oss-120b",
    "mistral:mistral-medium-2604",
    "groq:openai/gpt-oss-20b",
    "nvidia:nvidia/nemotron-3-super-120b-a12b",
    "cloudflare:@cf/qwen/qwen3-30b-a3b-fp8",
    "mistral:devstral-medium-latest",
    "groq:llama-3.1-8b-instant",
    "mistral:mistral-small-latest",
    "cloudflare:@cf/zai-org/glm-4.7-flash",
    "zai:glm-5.3",
    "gemini:gemini-3.1-pro-preview",
)


PROVIDER_KEY_ENV = {
    "nsu": "NSU_OPENWEBUI_KEY",
    "groq": "GROQ_API_KEY",
    "zai": "ZAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia": "NVIDIA_NIM_API_KEY",
    "cloudflare": "CLOUDFLARE_API_TOKEN",
}

OPENAI_BASE_URLS = {
    "nsu": "https://deepcode.ci.nsu.ru/api",
    "groq": "https://api.groq.com/openai/v1",
    "zai": "https://api.z.ai/api/paas/v4",
    "mistral": "https://api.mistral.ai/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class LLMUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LLMRoute:
    provider: str
    model: str
    key_env: str
    protocol: str

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True, slots=True)
class LLMAttempt:
    route: str
    ok: bool
    status: str
    latency_ms: int
    error: str = ""


def parse_route_specs(specs: str | None, *, allow_external_fallbacks: bool = False) -> tuple[LLMRoute, ...]:
    raw_specs = (
        tuple(item.strip() for item in specs.split(",") if item.strip())
        if specs and specs.strip()
        else (DEFAULT_ROUTE_SPECS if allow_external_fallbacks else DEFAULT_ROUTE_SPECS[:2])
    )

    routes: list[LLMRoute] = []
    for spec in raw_specs:
        provider, separator, model = spec.partition(":")
        provider = provider.strip().lower()
        model = model.strip()
        if not separator or not provider or not model:
            raise ValueError(f"Invalid CFT_LLM_ROUTES entry: {spec!r}")
        try:
            key_env = PROVIDER_KEY_ENV[provider]
        except KeyError as exc:
            raise ValueError(f"Unsupported LLM provider: {provider}") from exc

        protocol = (
            "gemini"
            if provider == "gemini"
            else "cloudflare"
            if provider == "cloudflare"
            else "openai"
        )
        routes.append(
            LLMRoute(
                provider=provider,
                model=model,
                key_env=key_env,
                protocol=protocol,
            )
        )

    if not routes:
        raise ValueError("At least one LLM route is required")
    return tuple(routes)


class ProviderFailoverClient:
    def __init__(
        self,
        *,
        routes: tuple[LLMRoute, ...],
        credentials: Mapping[str, str] | None = None,
        timeout_seconds: float = 25.0,
        max_output_tokens: int = 1200,
        reasoning_effort: str = "high",
        trace: bool = False,
    ) -> None:
        self.routes = routes
        self.credentials = {
            name: value.strip()
            for name, value in (credentials or {}).items()
            if value and value.strip()
        }
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        if reasoning_effort not in {"low", "high", "max"}:
            raise ValueError("reasoning_effort must be low, high or max")
        self.reasoning_effort = reasoning_effort
        self.trace = trace
        self.last_attempts: list[LLMAttempt] = []
        self.last_selected_route: LLMRoute | None = None

    def complete_model(
        self,
        *,
        output_model: type[TModel],
        system_prompt: str,
        user_payload: dict[str, Any],
        operation: str,
    ) -> TModel:
        schema = output_model.model_json_schema()
        user_prompt = (
            "Return exactly one JSON object and no markdown. "
            "The object MUST validate against this JSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}\n\n"
            "Input data:\n"
            f"{json.dumps(user_payload, ensure_ascii=False, default=str)}"
        )

        self.last_attempts = []
        self.last_selected_route = None
        blocked_providers: set[str] = set()

        for route in self.routes:
            if route.provider in blocked_providers:
                continue
            # Маршрут без секрета пропускается без сетевой попытки
            if not self._credential(route.key_env):
                continue
            if route.provider == "cloudflare" and not self._credential(
                "CLOUDFLARE_ACCOUNT_ID"
            ):
                continue

            started = time.monotonic()
            try:
                raw = self._request_route(route, system_prompt, user_prompt)
                payload = _extract_json_object(raw)
                result = output_model.model_validate(payload)
                elapsed = int((time.monotonic() - started) * 1000)
                self.last_attempts.append(
                    LLMAttempt(route=route.label, ok=True, status="ok", latency_ms=elapsed)
                )
                self.last_selected_route = route
                self._trace(operation, route, elapsed, "ok")
                return result
            except (RuntimeError, TypeError, ValueError) as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                status, block_provider = _classify_error(exc)
                message = redact_error_message(exc)
                self.last_attempts.append(
                    LLMAttempt(
                        route=route.label,
                        ok=False,
                        status=status,
                        latency_ms=elapsed,
                        error=message,
                    )
                )
                self._trace(operation, route, elapsed, status)
                if block_provider:
                    # Ошибка доступа или лимита блокирует провайдера до конца запроса
                    blocked_providers.add(route.provider)

        attempted = ", ".join(item.route for item in self.last_attempts) or "no configured route"
        raise LLMUnavailableError(
            f"No LLM route returned valid {output_model.__name__} for {operation}. "
            f"Attempted: {attempted}"
        )

    def _request_route(self, route: LLMRoute, system_prompt: str, user_prompt: str) -> str:
        if route.protocol == "openai":
            return self._request_openai(route, system_prompt, user_prompt)
        if route.protocol == "gemini":
            return self._request_gemini(route, system_prompt, user_prompt)
        if route.protocol == "cloudflare":
            return self._request_cloudflare(route, system_prompt, user_prompt)
        raise RuntimeError(f"Unsupported LLM protocol: {route.protocol}")

    def _request_openai(self, route: LLMRoute, system_prompt: str, user_prompt: str) -> str:
        key = self._required_credential(route.key_env)
        url = f"{OPENAI_BASE_URLS[route.provider]}/chat/completions"
        headers = {"Authorization": f"Bearer {key}"}
        if route.provider == "openrouter":
            headers["X-Title"] = "CFT Security Agent"

        payload = {
            "model": route.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": self.max_output_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        if route.provider == "nsu":
            payload["reasoning_effort"] = self.reasoning_effort
        result = _post_json(url, payload, headers=headers, timeout=self.timeout_seconds)
        choices = result.get("choices") or []
        if not choices:
            raise RuntimeError("response contains no choices")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("response contains no text content")
        return content

    def _request_gemini(self, route: LLMRoute, system_prompt: str, user_prompt: str) -> str:
        key = self._required_credential(route.key_env)
        model = urllib.parse.quote(route.model, safe="")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": self.max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        result = _post_json(
            url,
            payload,
            headers={"x-goog-api-key": key},
            timeout=self.timeout_seconds,
        )
        candidates = result.get("candidates") or []
        if not candidates:
            raise RuntimeError("response contains no candidates")
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        text = "".join(
            part.get("text", "") for part in parts if isinstance(part.get("text"), str)
        ).strip()
        if not text:
            raise RuntimeError("response contains no text content")
        return text

    def _request_cloudflare(
        self,
        route: LLMRoute,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        key = self._required_credential(route.key_env)
        account = self._required_credential("CLOUDFLARE_ACCOUNT_ID")
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{urllib.parse.quote(account)}/ai/v1/chat/completions"
        )
        payload = {
            "model": route.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": self.max_output_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        result = _post_json(
            url,
            payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=self.timeout_seconds,
        )
        choices = result.get("choices") or []
        if not choices:
            raise RuntimeError("response contains no choices")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("response contains no text content")
        return content

    def _credential(self, name: str) -> str:
        return self.credentials.get(name, "").strip()

    def _required_credential(self, name: str) -> str:
        value = self._credential(name)
        if not value:
            raise RuntimeError(f"{name} is not configured")
        return value

    def _trace(self, operation: str, route: LLMRoute, elapsed: int, status: str) -> None:
        if self.trace:
            print(
                f"[llm] {operation}: {route.label} -> {status} ({elapsed}ms)",
                file=sys.stderr,
            )


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "cft-security-agent/llm-fallback-v0.1",
        **headers,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        compact = " ".join(body.split())
        if len(compact) > 350:
            compact = compact[:347] + "..."
        raise RuntimeError(f"HTTP {exc.code}: {compact}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"network/timeout: {exc}") from None


def _extract_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        lines = lines[1:] if lines and lines[0].startswith("```") else lines
        lines = lines[:-1] if lines and lines[-1].strip() == "```" else lines
        value = "\n".join(lines).strip()
        if value.lower().startswith("json"):
            value = value[4:].lstrip()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model did not return a JSON object") from None
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid model JSON: {exc.msg}") from None

    if not isinstance(parsed, dict):
        raise TypeError("model returned JSON, but not an object")
    return parsed


def _classify_error(exc: Exception) -> tuple[str, bool]:
    message = str(exc).lower()
    if isinstance(exc, ValidationError):
        return "schema_validation_failed", False
    if "http 429" in message:
        return "rate_limited", True
    if "http 401" in message or "http 403" in message:
        return "auth_or_access", True
    if "http 402" in message:
        return "payment_required", True
    if "http 404" in message:
        return "model_or_endpoint_unavailable", False
    if "timeout" in message or "network" in message:
        return "network_error", False
    if "json" in message:
        return "invalid_structured_output", False
    return "request_failed", False
