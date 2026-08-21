import json

import pytest
from pydantic import ValidationError

from schemas.errors import ErrorDetail


def test_error_detail_serializes_stably() -> None:
    error = ErrorDetail(
        code="TIMEOUT",
        layer="executor",
        message="Sandbox execution timed out",
        retryable=True,
    )

    assert error.model_dump() == {
        "code": "TIMEOUT",
        "layer": "executor",
        "message": "Sandbox execution timed out",
        "retryable": True,
    }
    assert json.loads(error.model_dump_json()) == error.model_dump()


def test_error_detail_retryable_defaults_to_false() -> None:
    error = ErrorDetail(
        code="NOT_FOUND",
        layer="sast",
        message="Findings file was not found",
    )

    assert error.retryable is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "UNKNOWN_ERROR"),
        ("layer", "frontend"),
    ],
)
def test_error_detail_rejects_unknown_contract_values(field: str, value: str) -> None:
    payload = {
        "code": "INTERNAL_ERROR",
        "layer": "pipeline",
        "message": "Internal pipeline error",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        ErrorDetail.model_validate(payload)
