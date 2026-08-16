from __future__ import annotations

import argparse

from agent.llm import ProviderFailoverClient, parse_route_specs
from app.config import settings
from schemas.llm import LLMProbeResult


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe the configured CFT LLM fallback chain with a structured JSON response."
    )
    parser.add_argument(
        "--routes",
        default=settings.llm_routes,
        help="Optional comma-separated provider:model override.",
    )
    args = parser.parse_args()

    client = ProviderFailoverClient(
        routes=parse_route_specs(args.routes),
        credentials=settings.llm_provider_credentials(),
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=300,
        trace=True,
    )
    result = client.complete_model(
        output_model=LLMProbeResult,
        system_prompt=(
            "You are a connectivity check for a defensive security application. "
            "Do not call tools. Return only the requested JSON object."
        ),
        user_payload={
            "task": "Return status=ready and a short note that the model is reachable."
        },
        operation="probe",
    )

    route = client.last_selected_route
    if route is None:
        return 2
    print(f"Selected route: {route.label}")
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
