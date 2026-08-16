"""
Discover LLM models visible to the current API accounts.

Important:
- API keys are read from environment variables or a local .env file.
- Keys are never printed.
- By default this script only LISTS models. It does not send inference requests.
- "free_status" is exact only where the provider exposes enough metadata.
  Otherwise the script reports "account_accessible" / "unknown" instead of guessing.

Supported providers:
  groq, zai, mistral, gemini, openrouter, nvidia, cloudflare

Example:
  python scripts/discover_llm_models.py --env .env
  python scripts/discover_llm_models.py --env .env --json artifacts/llm-models.json
  python scripts/discover_llm_models.py --env .env --provider groq openrouter gemini
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TIMEOUT_SECONDS = 20

# Cloudflare's pricing docs currently call these paid-plan-only.
# Keep this intentionally tiny and visible instead of pretending the catalog
# gives a perfect machine-readable free/paid flag.
CLOUDFLARE_KNOWN_PAID_ONLY = {
    "@cf/moonshotai/kimi-k2.6",
    "@cf/moonshotai/kimi-k2.7-code",
    "@cf/zai-org/glm-5.2",
}

# Official Z.AI chat-completion docs list these model IDs. This is only used
# when a /models endpoint is not available. These are NOT automatically marked
# free because plan eligibility is account-specific.
ZAI_DOCUMENTED_CHAT_MODELS = [
    "glm-5.1",
    "glm-5-turbo",
    "glm-5",
    "glm-4.7",
    "glm-4.7-flash",
    "glm-4.7-flashx",
    "glm-4.6",
    "glm-4.5",
    "glm-4.5-air",
    "glm-4.5-x",
    "glm-4.5-airx",
    "glm-4.5-flash",
    "glm-4-32b-0414-128k",
]


@dataclass
class ModelInfo:
    provider: str
    model: str
    free_status: str
    chat_capable: bool | None = None
    tool_calling: bool | None = None
    structured_output: bool | None = None
    context_length: int | None = None
    source: str = "api"
    note: str = ""


@dataclass
class ProviderResult:
    provider: str
    ok: bool
    models: list[ModelInfo]
    error: str = ""


def load_dotenv(path: Path) -> None:
    """Small .env reader so the script has no external dependency."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and value[0] in {"'", '"'} and value[-1:] == value[0]:
            value = value[1:-1]

        # Do not overwrite variables explicitly exported by the shell.
        os.environ.setdefault(key, value)


def request_json(
    url: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "cft-security-agent-model-discovery/0.1",
    }
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if headers:
        request_headers.update(headers)

    req = urllib.request.Request(url, headers=request_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        # Never include request headers or credentials in errors.
        compact = " ".join(body.split())
        if len(compact) > 350:
            compact = compact[:347] + "..."
        raise RuntimeError(f"HTTP {exc.code}: {compact}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from None


def env_key(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def list_groq() -> ProviderResult:
    provider = "groq"
    try:
        payload = request_json(
            "https://api.groq.com/openai/v1/models",
            token=env_key("GROQ_API_KEY"),
        )
        models = []
        for item in payload.get("data", []):
            model_id = str(item.get("id", "")).strip()
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    provider=provider,
                    model=model_id,
                    free_status="account_accessible",
                    chat_capable=None,
                    tool_calling=None,
                    structured_output=None,
                    context_length=_int_or_none(item.get("context_window")),
                    note=(
                        "Groq /models exposes account-visible models, not a "
                        "machine-readable free/paid flag."
                    ),
                )
            )
        return ProviderResult(provider, True, models)
    except (RuntimeError, TypeError, ValueError) as exc:
        return ProviderResult(provider, False, [], str(exc))


def list_mistral() -> ProviderResult:
    provider = "mistral"
    try:
        payload = request_json(
            "https://api.mistral.ai/v1/models",
            token=env_key("MISTRAL_API_KEY"),
        )
        models = []
        for item in payload.get("data", []):
            if item.get("archived") is True:
                continue
            model_id = str(item.get("id", "")).strip()
            if not model_id:
                continue
            caps = item.get("capabilities") or {}
            models.append(
                ModelInfo(
                    provider=provider,
                    model=model_id,
                    free_status="account_accessible",
                    chat_capable=_bool_or_none(caps.get("completion_chat")),
                    tool_calling=_bool_or_none(caps.get("function_calling")),
                    structured_output=None,
                    context_length=_int_or_none(item.get("max_context_length")),
                    note=(
                        "Mistral returns models available to this account. "
                        "Free-mode eligibility is account/tier dependent."
                    ),
                )
            )
        return ProviderResult(provider, True, models)
    except (RuntimeError, TypeError, ValueError) as exc:
        return ProviderResult(provider, False, [], str(exc))


def list_openrouter() -> ProviderResult:
    provider = "openrouter"
    try:
        payload = request_json(
            "https://openrouter.ai/api/v1/models/user",
            token=env_key("OPENROUTER_API_KEY"),
        )
        models = []
        for item in payload.get("data", []):
            model_id = str(item.get("id", "")).strip()
            if not model_id:
                continue
            pricing = item.get("pricing") or {}
            is_free = model_id.endswith(":free") or _openrouter_pricing_is_free(pricing)
            supported = set(item.get("supported_parameters") or [])
            models.append(
                ModelInfo(
                    provider=provider,
                    model=model_id,
                    free_status="free" if is_free else "paid_or_unknown",
                    chat_capable=True,
                    tool_calling=("tools" in supported),
                    structured_output=bool(
                        {"structured_outputs", "response_format"} & supported
                    ),
                    context_length=_int_or_none(item.get("context_length")),
                    note="Free status derived from :free suffix or zero model pricing.",
                )
            )
        return ProviderResult(provider, True, models)
    except (RuntimeError, TypeError, ValueError) as exc:
        return ProviderResult(provider, False, [], str(exc))


def list_gemini() -> ProviderResult:
    provider = "gemini"
    try:
        key = env_key("GEMINI_API_KEY")
        models = []
        page_token = None

        while True:
            params = {"key": key, "pageSize": "1000"}
            if page_token:
                params["pageToken"] = page_token
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models?"
                + urllib.parse.urlencode(params)
            )
            payload = request_json(url)
            for item in payload.get("models", []):
                methods = set(item.get("supportedGenerationMethods") or [])
                if "generateContent" not in methods:
                    continue
                model_id = str(item.get("name", "")).removeprefix("models/")
                if not model_id:
                    continue
                models.append(
                    ModelInfo(
                        provider=provider,
                        model=model_id,
                        free_status="unknown",
                        chat_capable=True,
                        tool_calling=None,
                        structured_output=None,
                        context_length=_int_or_none(item.get("inputTokenLimit")),
                        note=(
                            "Gemini models.list does not expose free-tier pricing. "
                            "Cross-check with the current Gemini pricing page."
                        ),
                    )
                )
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        return ProviderResult(provider, True, models)
    except (RuntimeError, TypeError, ValueError) as exc:
        return ProviderResult(provider, False, [], str(exc))


def list_nvidia() -> ProviderResult:
    provider = "nvidia"
    try:
        payload = request_json(
            "https://integrate.api.nvidia.com/v1/models",
            token=env_key("NVIDIA_NIM_API_KEY"),
        )
        raw_models = payload.get("data", payload if isinstance(payload, list) else [])
        models = []
        for item in raw_models:
            model_id = str(item.get("id", "") if isinstance(item, dict) else item).strip()
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    provider=provider,
                    model=model_id,
                    free_status="account_accessible",
                    chat_capable=None,
                    tool_calling=None,
                    structured_output=None,
                    note=(
                        "NVIDIA API Catalog exposes many Free Endpoint models, "
                        "but /models does not provide a uniform free flag."
                    ),
                )
            )
        return ProviderResult(provider, True, models)
    except (RuntimeError, TypeError, ValueError) as exc:
        return ProviderResult(provider, False, [], str(exc))


def list_zai() -> ProviderResult:
    provider = "zai"
    key = os.getenv("ZAI_API_KEY", "").strip()
    if not key:
        return ProviderResult(provider, False, [], "ZAI_API_KEY is not set")

    try:
        payload = request_json(
            "https://api.z.ai/api/paas/v4/models",
            token=key,
        )
        raw_models = payload.get("data", payload if isinstance(payload, list) else [])
        models = []
        for item in raw_models:
            model_id = str(item.get("id", "") if isinstance(item, dict) else item).strip()
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    provider=provider,
                    model=model_id,
                    free_status="account_accessible",
                    chat_capable=None,
                    tool_calling=None,
                    structured_output=None,
                    note="Returned by the account's OpenAI-compatible /models endpoint.",
                )
            )
        if models:
            return ProviderResult(provider, True, models)
    except (RuntimeError, TypeError, ValueError) as exc:
        api_error = str(exc)
    else:
        api_error = "empty /models response"

    # Graceful fallback: useful for Z.AI keys whose endpoint does not implement
    # OpenAI-style model listing.
    models = [
        ModelInfo(
            provider=provider,
            model=model_id,
            free_status="unknown",
            chat_capable=True,
            tool_calling=True,
            structured_output=None,
            source="official_catalog_fallback",
            note=(
                "Documented Z.AI chat model; current account access was not "
                f"verified because /models failed: {api_error}"
            ),
        )
        for model_id in ZAI_DOCUMENTED_CHAT_MODELS
    ]
    return ProviderResult(provider, True, models, error=api_error)


def list_cloudflare() -> ProviderResult:
    provider = "cloudflare"
    try:
        token = env_key("CLOUDFLARE_API_TOKEN")
        account_id = env_key("CLOUDFLARE_ACCOUNT_ID")
        models = []
        page = 1

        while True:
            params = urllib.parse.urlencode(
                {
                    "page": page,
                    "per_page": 100,
                    "hide_experimental": "true",
                    "include_deprecated": "false",
                }
            )
            url = (
                "https://api.cloudflare.com/client/v4/accounts/"
                f"{urllib.parse.quote(account_id)}/ai/models/search?{params}"
            )
            payload = request_json(url, token=token)
            result = payload.get("result") or []
            if not result:
                break

            for item in result:
                if not isinstance(item, dict):
                    continue
                model_id = (
                    item.get("name")
                    or item.get("model")
                    or item.get("id")
                    or item.get("slug")
                )
                if not model_id:
                    continue
                model_id = str(model_id)
                task = str(item.get("task", "")).lower()
                chat_capable = (
                    "text-generation" in task
                    or "text generation" in task
                    or "text-to-text" in task
                    or task == ""
                )
                free_status = (
                    "paid_only_known"
                    if model_id in CLOUDFLARE_KNOWN_PAID_ONLY
                    else "free_quota_candidate"
                )
                models.append(
                    ModelInfo(
                        provider=provider,
                        model=model_id,
                        free_status=free_status,
                        chat_capable=chat_capable,
                        tool_calling=_capability_contains(item, "function"),
                        structured_output=None,
                        note=(
                            "Workers AI has a shared daily free allocation; "
                            "some explicitly paid-only models are excluded."
                        ),
                    )
                )

            # Cloudflare pagination metadata has varied across API wrappers.
            result_info = payload.get("result_info") or {}
            total_pages = _int_or_none(result_info.get("total_pages"))
            if total_pages is not None and page >= total_pages:
                break
            if len(result) < 100:
                break
            page += 1

        return ProviderResult(provider, True, models)
    except (RuntimeError, TypeError, ValueError) as exc:
        return ProviderResult(provider, False, [], str(exc))


def _openrouter_pricing_is_free(pricing: dict[str, Any]) -> bool:
    # For our text-agent use case, prompt/completion/request are the important
    # billable dimensions. Missing values are not treated as zero.
    wanted = ("prompt", "completion", "request")
    values = []
    for key in wanted:
        if key not in pricing:
            return False
        try:
            values.append(float(pricing[key]))
        except (TypeError, ValueError):
            return False
    return all(value == 0.0 for value in values)


def _capability_contains(item: dict[str, Any], needle: str) -> bool | None:
    haystack = json.dumps(item.get("properties") or item.get("capabilities") or "").lower()
    if not haystack:
        return None
    return needle.lower() in haystack


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


PROVIDERS = {
    "groq": list_groq,
    "zai": list_zai,
    "mistral": list_mistral,
    "gemini": list_gemini,
    "openrouter": list_openrouter,
    "nvidia": list_nvidia,
    "cloudflare": list_cloudflare,
}


def is_freeish(model: ModelInfo) -> bool:
    return model.free_status in {
        "free",
        "free_quota_candidate",
        "account_accessible",
        "unknown",
    }


def print_results(results: list[ProviderResult], *, only_freeish: bool) -> None:
    for result in results:
        print(f"\n=== {result.provider.upper()} ===")
        if not result.ok:
            print(f"ERROR: {result.error}")
            continue

        models = result.models
        if only_freeish:
            models = [m for m in models if is_freeish(m)]

        print(f"models: {len(models)}")
        if result.error:
            print(f"note: discovery fallback used ({result.error})")

        for model in sorted(models, key=lambda m: m.model.lower()):
            flags = [model.free_status]
            if model.tool_calling is True:
                flags.append("tools")
            elif model.tool_calling is False:
                flags.append("no-tools")
            if model.structured_output is True:
                flags.append("structured")
            if model.context_length:
                flags.append(f"ctx={model.context_length}")
            if model.source != "api":
                flags.append(model.source)
            print(f"- {model.model}  [{' | '.join(flags)}]")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List models visible through configured free/test LLM providers."
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to local .env file. Default: .env",
    )
    parser.add_argument(
        "--provider",
        nargs="+",
        choices=sorted(PROVIDERS),
        help="Only query selected providers.",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        help="Also write full machine-readable results to this path.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show entries marked paid/known-paid too. Default hides them.",
    )
    args = parser.parse_args()

    load_dotenv(Path(args.env))

    selected = args.provider or list(PROVIDERS)
    results = [PROVIDERS[name]() for name in selected]

    print_results(results, only_freeish=not args.all)

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                [
                    {
                        "provider": r.provider,
                        "ok": r.ok,
                        "error": r.error,
                        "models": [asdict(m) for m in r.models],
                    }
                    for r in results
                ],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nJSON written to: {target}")

    failures = [r.provider for r in results if not r.ok]
    if failures:
        print(
            "\nSome providers failed: " + ", ".join(failures),
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
