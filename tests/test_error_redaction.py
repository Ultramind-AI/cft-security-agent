import pytest

from schemas.errors import ErrorDetail
from security.error_redaction import REDACTION_MARKER, redact_error_message


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ("Authorization: Bearer abcdef123", "abcdef123"),
        ("Bearer standalone-token", "standalone-token"),
        ("GROQ_API_KEY=super-secret-value", "super-secret-value"),
        ("password=hunter2", "hunter2"),
        ('{"api_key":"secret-value"}', "secret-value"),
        ("https://john:secret@example.com/api", "secret"),
    ],
)
def test_redactor_removes_common_secret_patterns(message: str, secret: str) -> None:
    result = redact_error_message(message)

    assert secret not in result
    assert REDACTION_MARKER in result


def test_redactor_preserves_benign_meaning_and_normalizes_whitespace() -> None:
    assert redact_error_message("  connection\n refused  ") == "connection refused"


def test_redactor_hides_user_home_paths_but_keeps_relative_paths() -> None:
    message = "/Users/alice/project failed; C:\\Users\\Bob\\repo failed; backend/app.py"

    result = redact_error_message(message)

    assert "alice" not in result
    assert "Bob" not in result
    assert "backend/app.py" in result


def test_redactor_does_not_expose_traceback() -> None:
    message = (
        "Request failed\nTraceback (most recent call last):\n"
        '  File "/Users/alice/project/app.py", line 4\nRuntimeError: broken'
    )

    result = redact_error_message(message)

    assert result == "Request failed"
    assert "Traceback" not in result
    assert "alice" not in result


def test_redactor_bounds_long_messages_and_is_idempotent() -> None:
    message = f"token=secret-value {'x' * 1000}"

    result = redact_error_message(message, max_length=80)

    assert len(result) == 80
    assert "secret-value" not in result
    assert redact_error_message(result, max_length=80) == result


def test_error_detail_always_redacts_its_public_message() -> None:
    error = ErrorDetail(
        code="AUTH_ERROR",
        layer="llm",
        message="OPENROUTER_API_KEY=do-not-leak",
    )

    assert "do-not-leak" not in error.message
    assert REDACTION_MARKER in error.message
