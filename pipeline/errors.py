from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator

from executor.sandbox_session import SandboxSessionError
from sast.semgrep_runner import SemgrepError
from schemas.errors import ErrorCode, ErrorDetail, ErrorLayer
from security.error_redaction import redact_error_message


def error_from_exception(
    exc: Exception,
    *,
    layer: ErrorLayer,
    public_message: str,
) -> ErrorDetail:
    """На границе пайплайна маппим исключение без утечки raw text"""

    causes = tuple(_exception_chain(exc))
    code: ErrorCode
    retryable = False
    message = public_message

    if any(isinstance(item, (TimeoutError, subprocess.TimeoutExpired)) for item in causes):
        code = "TIMEOUT"
        retryable = True
    elif any(isinstance(item, FileNotFoundError) for item in causes):
        code = "NOT_FOUND"
    elif any(isinstance(item, json.JSONDecodeError) for item in causes):
        code = "INVALID_RESPONSE"
    elif any(isinstance(item, SandboxSessionError) for item in causes):
        code = "DEPENDENCY_UNAVAILABLE"
        retryable = True
        message = _sandbox_public_message(causes)
    elif isinstance(exc, SemgrepError):
        if "CLI was not found" in str(exc):
            code = "DEPENDENCY_UNAVAILABLE"
            retryable = True
        else:
            code = "EXECUTION_FAILED"
            message = "Semgrep scan failed while validating rules"
    elif any(isinstance(item, OSError) for item in causes):
        code = "DEPENDENCY_UNAVAILABLE"
        retryable = True
    elif isinstance(exc, (TypeError, ValueError)):
        code = "VALIDATION_ERROR"
    else:
        code = "INTERNAL_ERROR"

    return ErrorDetail(
        code=code,
        layer=layer,
        message=redact_error_message(message),
        retryable=retryable,
    )


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _sandbox_public_message(causes: tuple[BaseException, ...]) -> str:
    diagnostic = " ".join(str(item) for item in causes)
    if "port is already allocated" in diagnostic.lower():
        return "Sandbox could not start because a required host port is already allocated"
    if "cannot connect to the docker daemon" in diagnostic.lower():
        return "Sandbox could not connect to the local Docker daemon"
    if "docker compose command failed" in diagnostic.lower():
        return "Sandbox Docker Compose command failed"
    return "Managed sandbox failed to start"
