import json

import pytest

from agent.llm import LLMUnavailableError, ProviderFailoverClient, parse_route_specs
from schemas.llm import LLMProbeResult


def _client(_monkeypatch, specs: str) -> ProviderFailoverClient:
    routes = parse_route_specs(specs)
    credentials = {route.key_env: "test-key" for route in routes}
    return ProviderFailoverClient(
        routes=routes,
        credentials=credentials,
        timeout_seconds=1,
        max_output_tokens=100,
    )


def test_route_parser_preserves_model_suffixes() -> None:
    route = parse_route_specs("openrouter:openai/gpt-oss-20b:free")[0]
    assert route.provider == "openrouter"
    assert route.model == "openai/gpt-oss-20b:free"


def test_router_falls_back_after_invalid_structured_output(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        "groq:openai/gpt-oss-120b,mistral:mistral-large-latest",
    )
    responses = iter(["not-json", json.dumps({"status": "ready", "note": "ok"})])
    monkeypatch.setattr(client, "_request_route", lambda *args: next(responses))

    result = client.complete_model(
        output_model=LLMProbeResult,
        system_prompt="test",
        user_payload={"x": 1},
        operation="probe",
    )

    assert result.status == "ready"
    assert client.last_selected_route is not None
    assert client.last_selected_route.provider == "mistral"
    assert [item.ok for item in client.last_attempts] == [False, True]


def test_router_falls_back_after_schema_validation_failure(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        "groq:openai/gpt-oss-120b,mistral:mistral-large-latest",
    )
    responses = iter(
        [
            json.dumps({"status": "wrong", "note": "bad enum"}),
            json.dumps({"status": "ready", "note": "ok"}),
        ]
    )
    monkeypatch.setattr(client, "_request_route", lambda *args: next(responses))

    result = client.complete_model(
        output_model=LLMProbeResult,
        system_prompt="test",
        user_payload={},
        operation="probe",
    )

    assert result.status == "ready"
    assert client.last_attempts[0].status == "schema_validation_failed"


def test_rate_limit_blocks_remaining_routes_for_same_provider(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        (
            "groq:openai/gpt-oss-120b,"
            "groq:openai/gpt-oss-20b,"
            "mistral:mistral-large-latest"
        ),
    )
    calls = []

    def fake_request(route, *_args):
        calls.append(route.label)
        if route.provider == "groq":
            raise RuntimeError("HTTP 429: rate limit")
        return json.dumps({"status": "ready", "note": "ok"})

    monkeypatch.setattr(client, "_request_route", fake_request)
    result = client.complete_model(
        output_model=LLMProbeResult,
        system_prompt="test",
        user_payload={},
        operation="probe",
    )

    assert result.status == "ready"
    assert calls == [
        "groq/openai/gpt-oss-120b",
        "mistral/mistral-large-latest",
    ]


def test_router_raises_clean_error_when_all_routes_fail(monkeypatch) -> None:
    client = _client(monkeypatch, "groq:openai/gpt-oss-120b")
    monkeypatch.setattr(
        client,
        "_request_route",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("network/timeout: test")),
    )

    with pytest.raises(LLMUnavailableError, match="No LLM route returned valid"):
        client.complete_model(
            output_model=LLMProbeResult,
            system_prompt="test",
            user_payload={},
            operation="probe",
        )
