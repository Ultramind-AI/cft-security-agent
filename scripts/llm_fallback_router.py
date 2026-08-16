"""
Failover router / probe for CFT Security Agent LLM providers.

Reads credentials from a local .env or environment variables.
Never prints API keys.

The route order is intentionally provider-diversified: we try one strong model
from each provider before degrading to second-choice models. This is better for
demo reliability than burning through several models on one provider during a
provider-wide outage or quota event.

Usage:
  python scripts/llm_fallback_router.py --env .env --probe-all
  python scripts/llm_fallback_router.py --env .env --prompt "Return JSON with keys status and note"
  python scripts/llm_fallback_router.py --env .env --show-routes

Exit code:
  0 = at least one route produced valid JSON
  2 = every configured route failed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TIMEOUT_SECONDS = 25
MAX_OUTPUT_TOKENS = 350

# Operational ranking for THIS project, not a universal benchmark ranking.
# Wave 1: strongest practical candidate per provider.
# Wave 2+: degrade while preserving provider diversity.
ROUTES = [
    # Wave 1
    ("groq", "openai/gpt-oss-120b", "GROQ_API_KEY", "openai"),
    ("zai", "glm-5.3", "ZAI_API_KEY", "openai"),
    ("mistral", "mistral-large-latest", "MISTRAL_API_KEY", "openai"),
    ("gemini", "gemini-3.1-pro-preview", "GEMINI_API_KEY", "gemini"),
    ("nvidia", "openai/gpt-oss-120b", "NVIDIA_NIM_API_KEY", "openai"),
    ("cloudflare", "@cf/openai/gpt-oss-120b", "CLOUDFLARE_API_TOKEN", "cloudflare"),
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free", "OPENROUTER_API_KEY", "openai"),

    # Wave 2
    ("groq", "qwen/qwen3.6-27b", "GROQ_API_KEY", "openai"),
    ("zai", "glm-5.2", "ZAI_API_KEY", "openai"),
    ("mistral", "mistral-medium-2604", "MISTRAL_API_KEY", "openai"),
    ("gemini", "gemini-3.7-flash", "GEMINI_API_KEY", "gemini"),
    ("nvidia", "moonshotai/kimi-k2.6", "NVIDIA_NIM_API_KEY", "openai"),
    ("cloudflare", "@cf/nvidia/nemotron-3-120b-a12b", "CLOUDFLARE_API_TOKEN", "cloudflare"),
    ("openrouter", "google/gemma-4-31b-it:free", "OPENROUTER_API_KEY", "openai"),

    # Wave 3
    ("groq", "openai/gpt-oss-20b", "GROQ_API_KEY", "openai"),
    ("zai", "glm-5.1", "ZAI_API_KEY", "openai"),
    ("mistral", "devstral-medium-latest", "MISTRAL_API_KEY", "openai"),
    ("gemini", "gemini-3.5-flash", "GEMINI_API_KEY", "gemini"),
    ("nvidia", "nvidia/nemotron-3-super-120b-a12b", "NVIDIA_NIM_API_KEY", "openai"),
    ("cloudflare", "@cf/qwen/qwen3-30b-a3b-fp8", "CLOUDFLARE_API_TOKEN", "cloudflare"),
    ("openrouter", "openai/gpt-oss-20b:free", "OPENROUTER_API_KEY", "openai"),

    # Final lightweight fallbacks
    ("groq", "llama-3.1-8b-instant", "GROQ_API_KEY", "openai"),
    ("mistral", "mistral-small-latest", "MISTRAL_API_KEY", "openai"),
    ("gemini", "gemini-2.5-flash-lite", "GEMINI_API_KEY", "gemini"),
    ("openrouter", "nvidia/nemotron-nano-9b-v2:free", "OPENROUTER_API_KEY", "openai"),
    ("cloudflare", "@cf/zai-org/glm-4.7-flash", "CLOUDFLARE_API_TOKEN", "cloudflare"),
]


OPENAI_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "zai": "https://api.z.ai/api/paas/v4",
    "mistral": "https://api.mistral.ai/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


@dataclass(frozen=True)
class Route:
    priority: int
    provider: str
    model: str
    key_env: str
    protocol: str


@dataclass
class Attempt:
    priority: int
    provider: str
    model: str
    ok: bool
    latency_ms: int
    status: str
    error: str = ""
    response: dict[str, Any] | None = None


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def routes() -> list[Route]:
    return [
        Route(index + 1, provider, model, key_env, protocol)
        for index, (provider, model, key_env, protocol) in enumerate(ROUTES)
    ]


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "cft-security-agent-llm-router/0.1",
        **headers,
    }
    req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        compact = " ".join(body.split())
        if len(compact) > 400:
            compact = compact[:397] + "..."
        raise RuntimeError(f"HTTP {exc.code}: {compact}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"network/timeout: {exc}") from None


def call_openai_compatible(route: Route, system: str, prompt: str) -> str:
    key = os.getenv(route.key_env, "").strip()
    if not key:
        raise RuntimeError(f"{route.key_env} is not set")

    base = OPENAI_BASE_URLS[route.provider]
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}"}

    # Helpful OpenRouter attribution headers. They are not secrets.
    if route.provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/Ultramind-AI/cft-security-agent"
        headers["X-Title"] = "CFT Security Agent"

    payload: dict[str, Any] = {
        "model": route.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
    }

    # JSON object mode is widely accepted and less brittle across providers than
    # provider-specific JSON Schema dialects. The project layer should still
    # validate the returned object with Pydantic before trusting it.
    payload["response_format"] = {"type": "json_object"}

    result = post_json(url, payload, headers=headers)
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError("response contains no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("response contains no text content")
    return content


def call_gemini(route: Route, system: str, prompt: str) -> str:
    key = os.getenv(route.key_env, "").strip()
    if not key:
        raise RuntimeError(f"{route.key_env} is not set")

    model = urllib.parse.quote(route.model, safe="")
    query = urllib.parse.urlencode({"key": key})
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?{query}"
    )

    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
        },
    }
    result = post_json(url, payload, headers={})
    candidates = result.get("candidates") or []
    if not candidates:
        raise RuntimeError("response contains no candidates")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    texts = [part.get("text") for part in parts if isinstance(part.get("text"), str)]
    content = "".join(texts).strip()
    if not content:
        raise RuntimeError("response contains no text content")
    return content


def call_cloudflare(route: Route, system: str, prompt: str) -> str:
    token = os.getenv(route.key_env, "").strip()
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not token:
        raise RuntimeError(f"{route.key_env} is not set")
    if not account:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID is not set")

    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{urllib.parse.quote(account)}/ai/v1/chat/completions"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "cf-aig-gateway-id": "default",
    }
    payload = {
        "model": route.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    result = post_json(url, payload, headers=headers)
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError("response contains no choices")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("response contains no text content")
    return content


def extract_json(text: str) -> dict[str, Any]:
    value = text.strip()

    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
        if value.lower().startswith("json"):
            value = value[4:].lstrip()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("model did not return a JSON object") from None
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON: {exc.msg}") from None

    if not isinstance(parsed, dict):
        raise TypeError("model returned JSON, but not an object")
    return parsed


def classify_error(message: str) -> tuple[str, bool]:
    """Return (status, block_provider_for_this_run)."""
    lower = message.lower()
    if "http 429" in lower:
        return "rate_limited", True
    if "http 401" in lower or "http 403" in lower:
        return "auth_or_access", True
    if "http 402" in lower:
        return "payment_required", True
    if "http 404" in lower:
        return "model_or_endpoint_unavailable", False
    if "timeout" in lower or "network" in lower:
        return "network_error", False
    if "json" in lower:
        return "invalid_structured_output", False
    return "request_failed", False


def attempt_route(route: Route, system: str, prompt: str) -> Attempt:
    start = time.monotonic()
    try:
        if route.protocol == "openai":
            text = call_openai_compatible(route, system, prompt)
        elif route.protocol == "gemini":
            text = call_gemini(route, system, prompt)
        elif route.protocol == "cloudflare":
            text = call_cloudflare(route, system, prompt)
        else:
            raise RuntimeError(f"unsupported protocol: {route.protocol}")

        parsed = extract_json(text)
        elapsed = int((time.monotonic() - start) * 1000)
        return Attempt(
            priority=route.priority,
            provider=route.provider,
            model=route.model,
            ok=True,
            latency_ms=elapsed,
            status="ok",
            response=parsed,
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        status, _ = classify_error(str(exc))
        return Attempt(
            priority=route.priority,
            provider=route.provider,
            model=route.model,
            ok=False,
            latency_ms=elapsed,
            status=status,
            error=str(exc),
        )


def run_fallback(
    system: str,
    prompt: str,
    *,
    collect_all: bool,
) -> tuple[Attempt | None, list[Attempt]]:
    blocked_providers: set[str] = set()
    attempts: list[Attempt] = []
    first_success: Attempt | None = None

    for route in routes():
        if route.provider in blocked_providers:
            continue
        if not os.getenv(route.key_env, "").strip():
            continue
        if route.provider == "cloudflare" and not os.getenv(
            "CLOUDFLARE_ACCOUNT_ID", ""
        ).strip():
            continue

        attempt = attempt_route(route, system, prompt)
        attempts.append(attempt)

        marker = "OK" if attempt.ok else "FAIL"
        print(
            f"[{marker}] #{attempt.priority:02d} "
            f"{attempt.provider}/{attempt.model} "
            f"{attempt.latency_ms}ms {attempt.status}"
        )

        if attempt.ok and first_success is None:
            first_success = attempt
            if not collect_all:
                return first_success, attempts

        if not attempt.ok:
            _, block_provider = classify_error(attempt.error)
            if block_provider:
                blocked_providers.add(route.provider)

    return first_success, attempts


def main() -> int:
    parser = argparse.ArgumentParser(description="CFT multi-provider LLM fallback router.")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--show-routes", action="store_true")
    parser.add_argument("--probe-all", action="store_true")
    parser.add_argument(
        "--prompt",
        default=(
            'Return exactly one JSON object with keys "status" and "note". '
            'Set status to "ready" and note to a short sentence.'
        ),
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default="artifacts/llm-probe.json",
        help="Where --probe-all writes the attempt report.",
    )
    args = parser.parse_args()

    load_dotenv(Path(args.env))

    if args.show_routes:
        for route in routes():
            configured = bool(os.getenv(route.key_env, "").strip())
            print(
                f"#{route.priority:02d} {route.provider:11s} "
                f"{route.model} [{'configured' if configured else 'no-key'}]"
            )
        return 0

    system = (
        "You are a backend component in a security-analysis application. "
        "For this connectivity test, do not call tools and do not include "
        "markdown. Return only the JSON object requested by the user."
    )

    first_success, attempts = run_fallback(
        system,
        args.prompt,
        collect_all=args.probe_all,
    )

    if args.probe_all:
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "first_success": (
                        {
                            "provider": first_success.provider,
                            "model": first_success.model,
                            "priority": first_success.priority,
                        }
                        if first_success
                        else None
                    ),
                    "attempts": [asdict(a) for a in attempts],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nProbe report: {path}")

    if first_success:
        print(
            f"\nSELECTED: {first_success.provider}/{first_success.model} "
            f"(priority #{first_success.priority})"
        )
        print(json.dumps(first_success.response, ensure_ascii=False, indent=2))
        return 0

    print("\nNo route produced valid JSON.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
