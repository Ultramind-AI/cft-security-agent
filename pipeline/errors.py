from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator

from sast.semgrep_runner import SemgrepError
from security.error_redaction import redact_error_message
from schemas.errors import ErrorCode, ErrorDetail, ErrorLayer


def error_from_exception(
    exc: Exception,
    *,
    layer: ErrorLayer,
    public_message: str,
) -> ErrorDetail:
    """Map an exception at a pipeline boundary without exposing its raw text."""

    causes = tuple(_exception_chain(exc))
    code: ErrorCode
    retryable = False

    if any(isinstance(item, (TimeoutError, subprocess.TimeoutExpired)) for item in causes):
        code = "TIMEOUT"
        retryable = True
    elif any(isinstance(item, FileNotFoundError) for item in causes):
        code = "NOT_FOUND"
    elif any(isinstance(item, json.JSONDecodeError) for item in causes):
        code = "INVALID_RESPONSE"
    elif isinstance(exc, SemgrepError) or any(isinstance(item, OSError) for item in causes):
        code = "DEPENDENCY_UNAVAILABLE"
        retryable = True
    elif isinstance(exc, (TypeError, ValueError)):
        code = "VALIDATION_ERROR"
    else:
        code = "INTERNAL_ERROR"

    return ErrorDetail(
        code=code,
        layer=layer,
        message=redact_error_message(public_message),
        retryable=retryable,
    )


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__
