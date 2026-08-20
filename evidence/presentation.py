from __future__ import annotations


def format_evidence_scope(details: dict[str, object]) -> str | None:
    """Возвращает компактную строку области для структурированного Evidence."""
    raw_scope = details.get("scope")
    if raw_scope is None:
        return None

    scope = str(raw_scope).replace("_", "-")
    if scope == "source":
        scope = "source-only"

    verification_flags = [
        f"{key}={value}"
        for key, value in sorted(details.items())
        if key.endswith("_verified")
    ]
    suffix = f" ({', '.join(verification_flags)})" if verification_flags else ""
    return f"Evidence scope: {scope}{suffix}"
