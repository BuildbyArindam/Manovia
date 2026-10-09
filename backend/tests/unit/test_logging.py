"""Unit tests for structured logging and the sensitive-field redaction processor."""

import io
import json

import structlog
from hypothesis import given
from hypothesis import strategies as st

from app.core.logging import (
    SENSITIVE_LOG_FIELDS,
    configure_logging,
    drop_sensitive_fields,
)

USER_TEXT_SENTINEL = "USER-TEXT-SENTINEL-7f3a9c"


def test_drop_sensitive_fields_removes_blocked_fields() -> None:
    event_dict = {
        "event": "incoming message",
        "message_text": USER_TEXT_SENTINEL,
        "content": USER_TEXT_SENTINEL,
        "body": USER_TEXT_SENTINEL,
        "note": USER_TEXT_SENTINEL,
        "request_id": "req-1",
    }
    result = drop_sensitive_fields(None, "info", event_dict)
    assert result == {"event": "incoming message", "request_id": "req-1"}


def test_drop_sensitive_fields_recurses_into_nested_structures() -> None:
    event_dict = {
        "event": "incoming message",
        "metadata": {"content": USER_TEXT_SENTINEL, "attempt": 1},
        "items": [{"note": USER_TEXT_SENTINEL}, {"ok": True}],
    }
    result = drop_sensitive_fields(None, "info", event_dict)
    assert result == {
        "event": "incoming message",
        "metadata": {"attempt": 1},
        "items": [{}, {"ok": True}],
    }


def test_rendered_json_logs_never_contain_user_text() -> None:
    """Prove that user text passed as a log field never reaches the log output."""
    buffer = io.StringIO()
    configure_logging()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(buffer))
    try:
        logger = structlog.get_logger()
        logger.info(
            "incoming message",
            message_text=USER_TEXT_SENTINEL,
            content=USER_TEXT_SENTINEL,
            body=USER_TEXT_SENTINEL,
            note=USER_TEXT_SENTINEL,
            request_id="req-42",
        )
    finally:
        configure_logging()  # restore the stdout logger factory

    output = buffer.getvalue()
    assert USER_TEXT_SENTINEL not in output
    record = json.loads(output.strip().splitlines()[-1])
    assert record["event"] == "incoming message"
    assert record["request_id"] == "req-42"
    assert record["level"] == "info"


@given(
    st.dictionaries(
        keys=st.sampled_from(sorted(SENSITIVE_LOG_FIELDS | {"event", "request_id", "status_code"})),
        values=st.text(),
        max_size=8,
    )
)
def test_redaction_holds_for_arbitrary_event_dicts(event_dict: dict[str, str]) -> None:
    result = drop_sensitive_fields(None, "info", event_dict)
    for field in SENSITIVE_LOG_FIELDS:
        assert field not in result
    for key, value in event_dict.items():
        if key not in SENSITIVE_LOG_FIELDS:
            assert result[key] == value
