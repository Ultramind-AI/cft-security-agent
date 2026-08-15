from schemas.finding import Finding

def normalize_semgrep_result(raw: dict) -> Finding:
    extra = raw.get("extra", {})
    start = raw.get("start", {})
    end = raw.get("end", {})
    return Finding(
        id=str(raw.get("check_id", "unknown")) + ":" + str(start.get("line", 0)),
        source="semgrep",
        rule_id=str(raw.get("check_id", "unknown")),
        title=str(extra.get("message", raw.get("check_id", "Semgrep finding"))),
        description=str(extra.get("message", "")),
        file=str(raw.get("path", "")),
        line_start=start.get("line"),
        line_end=end.get("line"),
        severity=extra.get("severity"),
        service=None,
    )
