"""Fixed capability worker. It is intentionally not a command interpreter."""

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


def _read_payload() -> dict:
    data = sys.stdin.buffer.read(64 * 1024)
    if not data:
        raise ValueError("Missing sandbox request")
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Sandbox request must be an object")
    return payload


def _fixed_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid trusted target URL")
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def _http_get(url: str, timeout: float, output_limit: int) -> tuple[int, str, str]:
    request = Request(
        url,
        headers={"User-Agent": "cft-security-agent-executor/0.3"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(output_limit + 1)
            text = body[:output_limit].decode("utf-8", errors="replace")
            if len(body) > output_limit:
                text = f"{text}\n...[truncated]"
            if 200 <= int(response.status) < 300:
                return 0, text, ""
            return 1, text, f"Unexpected HTTP status: {response.status}"
    except HTTPError as exc:
        body = exc.read(output_limit + 1)
        text = body[:output_limit].decode("utf-8", errors="replace")
        return 1, text, f"HTTP request failed with status {exc.code}"
    except URLError as exc:
        return 1, "", f"HTTP request failed: {exc.reason}"


def _safe_noop(parameters: dict) -> tuple[int, str, str]:
    allowed = {"message", "test_outcome"}
    unexpected = sorted(set(parameters) - allowed)
    if unexpected:
        return 2, "", f"Unsupported safe_noop parameters: {unexpected}"

    message = str(parameters.get("message", "ok"))[:256]
    outcome = str(parameters.get("test_outcome", "confirmed"))
    if outcome not in {"confirmed", "rejected", "inconclusive"}:
        return 2, "", "Invalid safe_noop test_outcome"
    return 0, f"safe_noop:{message}:outcome={outcome}", ""


def _execute(payload: dict) -> tuple[int, str, str]:
    tool = str(payload.get("tool", ""))
    base_url = str(payload.get("base_url", ""))
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, dict):
        return 2, "", "Capability parameters must be an object"

    timeout = float(payload.get("request_timeout_seconds", 1.0))
    output_limit = int(payload.get("max_output_bytes", 16_384))

    if tool == "safe_noop":
        return _safe_noop(parameters)

    if parameters:
        return 2, "", f"{tool} does not accept ActionProposal parameters"

    if tool == "check_sberlab_health":
        exit_code, stdout, stderr = _http_get(
            _fixed_url(base_url, "/health/"),
            timeout,
            output_limit,
        )
        if exit_code != 0:
            return exit_code, stdout, stderr
        try:
            health = json.loads(stdout)
        except json.JSONDecodeError:
            return 1, stdout, "Health endpoint returned invalid JSON"
        if health.get("status") != "ok" or health.get("database") != "ok":
            return 1, stdout, "SberLab health response is not ready"
        return 0, stdout, ""

    if tool == "get_sberlab_public_projects":
        return _http_get(
            _fixed_url(base_url, "/api/projects/"),
            timeout,
            output_limit,
        )

    return 126, "", f"Unknown worker capability: {tool}"


def main() -> int:
    try:
        exit_code, stdout, stderr = _execute(_read_payload())
    except (OSError, TypeError, ValueError) as exc:
        exit_code = 1
        stdout = ""
        stderr = f"Worker failed: {type(exc).__name__}"

    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, file=sys.stderr, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
