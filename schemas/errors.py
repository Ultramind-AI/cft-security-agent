from typing import Literal

from pydantic import BaseModel, field_validator

from security.error_redaction import redact_error_message

ErrorCode = Literal[
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "TIMEOUT",
    "BUILD_FAILED",
    "UNSUPPORTED_RUNTIME",
    "ISOLATION_BLOCKED",
    "DEPENDENCY_UNAVAILABLE",
    "PERSISTENCE_ERROR",
    "INVALID_RESPONSE",
    "RATE_LIMITED",
    "AUTH_ERROR",
    "EXECUTION_FAILED",
    "INTERNAL_ERROR",
]

ErrorLayer = Literal[
    "sast",
    "agent",
    "llm",
    "validator",
    "executor",
    "storage",
    "pipeline",
]


class ErrorDetail(BaseModel):
    code: ErrorCode
    layer: ErrorLayer
    message: str
    retryable: bool = False

    @field_validator("message")
    @classmethod
    def redact_public_message(cls, value: str) -> str:
        return redact_error_message(value)
