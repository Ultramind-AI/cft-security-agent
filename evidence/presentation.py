from __future__ import annotations


def format_evidence_scope(
    description: str,
    facts: dict[str, object],
) -> str | None:
    """Возвращает компактную строку области для структурированного док-ва."""
    if not description:
        return None

    scope = description.replace("_", "-")
    if scope == "source":
        scope = "source-only"

    verification_flags = [
        f"{key}={value}"
        for key, value in sorted(facts.items())
        if key.endswith("_verified")
    ]
    suffix = f" ({', '.join(verification_flags)})" if verification_flags else ""
    return f"Evidence scope: {scope}{suffix}"
