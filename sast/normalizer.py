from collections.abc import Callable

from schemas.finding import Finding

ServiceResolver = Callable[[str], str | None]


def normalize_semgrep_result(
    raw: dict,
    *,
    service_resolver: ServiceResolver | None = None,
) -> Finding:
    extra = raw.get("extra", {})
    start = raw.get("start", {})
    end = raw.get("end", {})

    check_id = str(raw.get("check_id", "unknown"))
    path = str(raw.get("path", ""))
    line_start = start.get("line")
    stable_location = line_start if line_start is not None else 0
    message = str(extra.get("message", ""))

    # Semgrep не знает архитектуру проекта, service приходит из TargetProfile
    service = service_resolver(path) if service_resolver is not None else None

    return Finding(
        id=f"{check_id}:{path}:{stable_location}",
        source="semgrep",
        rule_id=check_id,
        title=message or check_id or "Semgrep finding",
        description=message,
        file=path,
        line_start=line_start,
        line_end=end.get("line"),
        severity=extra.get("severity"),
        service=service,
    )


def normalize_semgrep_payload(
    payload: dict,
    *,
    service_resolver: ServiceResolver | None = None,
) -> list[Finding]:
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise TypeError("Semgrep JSON field 'results' must be a list")
    return [
        normalize_semgrep_result(item, service_resolver=service_resolver)
        for item in results
        if isinstance(item, dict)
    ]
