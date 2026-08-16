"""Fixed capability worker. It is intentionally not a command interpreter."""

import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

_DOCKERFILE_USER_TOOL = "check_sberlab_backend_dockerfile_user"
_DOCKERFILE_RELATIVE_PATH = Path("backend/Dockerfile")
_MAX_DOCKERFILE_BYTES = 256 * 1024


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


def _read_fixed_backend_dockerfile(repository_path: str) -> str:
    if not repository_path:
        raise ValueError("Trusted target repository path is not configured")

    try:
        root = Path(repository_path).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("Trusted target repository is unavailable") from exc

    try:
        dockerfile = (root / _DOCKERFILE_RELATIVE_PATH).resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("Trusted backend Dockerfile is unavailable") from exc

    try:
        dockerfile.relative_to(root)
    except ValueError as exc:
        raise ValueError("Fixed Dockerfile path escaped trusted target repository") from exc

    if not dockerfile.is_file():
        raise ValueError("Trusted backend Dockerfile is not a regular file")
    if dockerfile.stat().st_size > _MAX_DOCKERFILE_BYTES:
        raise ValueError("Trusted backend Dockerfile exceeds verification size limit")

    return dockerfile.read_text(encoding="utf-8")


def _final_stage_user(dockerfile_text: str) -> tuple[int, str | None, int | None]:
    stage_count = 0
    final_user: str | None = None
    final_user_line: int | None = None

    for line_number, raw_line in enumerate(dockerfile_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 1)
        instruction = parts[0].upper()
        argument = parts[1].strip() if len(parts) == 2 else ""

        if instruction == "FROM":
            if not argument:
                raise ValueError("Malformed FROM instruction in backend Dockerfile")
            stage_count += 1
            final_user = None
            final_user_line = None
            continue

        if instruction == "USER" and stage_count > 0:
            if not argument:
                raise ValueError("Malformed USER instruction in backend Dockerfile")
            final_user = argument
            final_user_line = line_number

    if stage_count == 0:
        raise ValueError("Backend Dockerfile contains no FROM instruction")

    return stage_count, final_user, final_user_line


def _check_backend_dockerfile_user(repository_path: str) -> tuple[int, str, str]:
    dockerfile_text = _read_fixed_backend_dockerfile(repository_path)
    final_stage, user, user_line = _final_stage_user(dockerfile_text)
    user_present = user is not None
    verdict = "rejected" if user_present else "confirmed"

    explanation = (
        "Final backend Dockerfile stage explicitly sets USER; the reported missing-USER "
        "source condition is not present."
        if user_present
        else (
            "Final backend Dockerfile stage has no explicit USER directive; the reported "
            "missing-USER source condition is present. This is source evidence only and "
            "does not prove the runtime container UID."
        )
    )

    result = {
        "schema": "cft.dockerfile_user_check.v1",
        "dockerfile": _DOCKERFILE_RELATIVE_PATH.as_posix(),
        "final_stage": final_stage,
        "user_directive_present": user_present,
        "user": user,
        "user_line": user_line,
        "verdict": verdict,
        "scope": "source",
        "runtime_user_verified": False,
        "explanation": explanation,
    }
    return 0, json.dumps(result, ensure_ascii=False, separators=(",", ":")), ""


def _execute(payload: dict) -> tuple[int, str, str]:
    tool = str(payload.get("tool", ""))
    base_url = str(payload.get("base_url", ""))
    repository_path = str(payload.get("repository_path", ""))
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, dict):
        return 2, "", "Capability parameters must be an object"

    timeout = float(payload.get("request_timeout_seconds", 1.0))
    output_limit = int(payload.get("max_output_bytes", 16_384))

    if tool == "safe_noop":
        return _safe_noop(parameters)

    if parameters:
        return 2, "", f"{tool} does not accept ActionProposal parameters"

    if tool == _DOCKERFILE_USER_TOOL:
        try:
            return _check_backend_dockerfile_user(repository_path)
        except (OSError, UnicodeError, ValueError) as exc:
            return 1, "", f"Dockerfile USER check failed: {type(exc).__name__}: {exc}"

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
        stderr = f"Worker failed: {type(exc).__name__}: {exc}"

    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, file=sys.stderr, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
