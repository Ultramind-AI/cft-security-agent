from typing import Literal

from pydantic import BaseModel

ErrorCode = Literal[
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "TIMEOUT",
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
