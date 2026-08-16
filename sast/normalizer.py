from pathlib import PurePosixPath

from schemas.finding import Finding


def _infer_service(path: str) -> str | None:
    """Map a repository path to the coarse SberLab component used by the MVP."""
    normalized = path.replace("\\", "/").lstrip("./")
    parts = PurePosixPath(normalized).parts
    if not parts:
        return None
    if parts[0] == "backend":
        return "backend"
    if parts[0] == "frontend":
        return "frontend"
    return None


def normalize_semgrep_result(raw: dict) -> Finding:
    extra = raw.get("extra", {})
    start = raw.get("start", {})
    end = raw.get("end", {})

    check_id = str(raw.get("check_id", "unknown"))
    path = str(raw.get("path", ""))
    line_start = start.get("line")
    stable_location = line_start if line_start is not None else 0

    message = str(extra.get("message", ""))
    title = message or check_id or "Semgrep finding"

    return Finding(
        id=f"{check_id}:{path}:{stable_location}",
        source="semgrep",
        rule_id=check_id,
        title=title,
        description=message,
        file=path,
        line_start=line_start,
        line_end=end.get("line"),
        severity=extra.get("severity"),
        service=_infer_service(path),
    )


def normalize_semgrep_payload(payload: dict) -> list[Finding]:
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise TypeError("Semgrep JSON field 'results' must be a list")
    return [normalize_semgrep_result(item) for item in results if isinstance(item, dict)]
