"""Day 27: Sentry events never carry user text — the before_send contract.

Two layers:

* pure-function tests on ``scrub_sentry_event``: an event with a sentinel in
  every possible hiding place comes back without any of them;
* one end-to-end test through the real app with a ``DummyTransport``: a 500
  raised while a sentinel body (and PII in the exception message) is in
  flight, and the event Sentry would actually ship is searched.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from app.core import crypto
from app.core.config import Settings
from app.core.sentry import init_sentry, scrub_sentry_event
from app.main import create_app

SENTINEL = "SENTINEL-SENTRY-3e8f42-do-not-ship"
EMAIL = "person@example.com"
PHONE = "9876543210"

TEST_DSN = "https://public-key@sentry.invalid/42"


class CapturingTransport(Transport):  # type: ignore[misc]
    """Stands in for the HTTP transport: records every event, ships nothing."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def capture_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def capture_envelope(self, envelope: Any) -> None:  # noqa: RUF100
        for item in envelope.items:
            raw = getattr(item.payload, "json", None)
            if raw is None:
                continue
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", "replace")
            try:
                event = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(event, dict):
                self.events.append(event)


def event_without_sentinel(event: dict[str, Any]) -> bool:
    return SENTINEL not in json.dumps(event, default=str)


# --- the pure scrubber --------------------------------------------------------


def test_a_request_context_is_reduced_to_the_allowlist() -> None:
    event = {
        "request": {
            "url": f"https://api.test/api/v1/journal?draft={SENTINEL}",
            "method": "POST",
            "data": {"title": SENTINEL},
            "query_string": f"draft={SENTINEL}",
            "cookies": {"session": SENTINEL},
            "headers": {
                "Content-Type": "application/json",
                "X-Request-ID": "abc-123",
                "Authorization": f"Bearer {SENTINEL}",
                "Cookie": f"session={SENTINEL}",
            },
        }
    }
    scrubbed = scrub_sentry_event(event, hint=None)
    assert event_without_sentinel(scrubbed)
    request = scrubbed["request"]
    assert "data" not in request
    assert not request.get("query_string")
    assert "cookies" not in request
    assert request["url"].endswith("/api/v1/journal")
    assert set(request["headers"]) <= {"Content-Type", "X-Request-ID"}


def test_blocked_keys_are_dropped_everywhere() -> None:
    event = {
        "extra": {
            "message_text": SENTINEL,
            "note": SENTINEL,
            "token": SENTINEL,
            "nested": {"body": SENTINEL, "level": 3},
            "safe": "ordinary text",
        },
        "tags": {"secret_key": SENTINEL, "route": "journal"},
    }
    scrubbed = scrub_sentry_event(event, hint=None)
    assert event_without_sentinel(scrubbed)
    assert scrubbed["extra"]["safe"] == "ordinary text"
    assert scrubbed["extra"]["nested"] == {"level": 3}
    assert scrubbed["tags"] == {"route": "journal"}


def test_exception_messages_are_pii_redacted_but_types_remain() -> None:
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": f"could not store draft for {EMAIL} on {PHONE}",
                    "mechanism": {"type": "generic"},
                }
            ]
        }
    }
    scrubbed = scrub_sentry_event(event, hint=None)
    assert event_without_sentinel(scrubbed)
    assert EMAIL not in json.dumps(scrubbed)
    assert PHONE not in json.dumps(scrubbed)
    value = scrubbed["exception"]["values"][0]["value"]
    assert "ValueError" not in value  # the type stays in its own field
    assert scrubbed["exception"]["values"][0]["type"] == "ValueError"
    assert "[EMAIL" in value or "[" in value  # replaced by a typed placeholder


def test_logentry_message_and_breadcrumbs_are_redacted() -> None:
    """Free-text fields pass through the PII redactor (curated log messages
    never carry user text by policy — the redactor is the second wall)."""
    event = {
        "message": f"failed while saving {EMAIL}",
        "logentry": {"message": f"could not reach {PHONE}", "params": []},
        "breadcrumbs": {
            "values": [
                {"message": f"requested {EMAIL}", "type": "http"},
                {"message": "plain", "type": "default"},
            ]
        },
    }
    scrubbed = scrub_sentry_event(event, hint=None)
    assert EMAIL not in json.dumps(scrubbed, default=str)
    assert PHONE not in json.dumps(scrubbed, default=str)
    assert scrubbed["breadcrumbs"]["values"][1]["message"] == "plain"


def test_stacktrace_frame_locals_are_stripped() -> None:
    """Frame ``vars`` is where a parsed request body actually hides — they go,
    while the type, locations and source context lines stay for debugging."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": f"failed handling {EMAIL}",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "app/api/v1/journal.py",
                                "function": "create_journal_entry",
                                "lineno": 42,
                                "context_line": "entry = await repo.create(",
                                "vars": {"payload": SENTINEL, "user_id": "u-1"},
                            }
                        ]
                    },
                }
            ]
        },
        "threads": {
            "values": [
                {
                    "id": 1,
                    "stacktrace": {"frames": [{"filename": "a.py", "vars": {"note": SENTINEL}}]},
                }
            ]
        },
    }
    scrubbed = scrub_sentry_event(event, hint=None)
    assert event_without_sentinel(scrubbed)
    frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "vars" not in frame
    assert frame["filename"] == "app/api/v1/journal.py"
    assert frame["lineno"] == 42
    assert frame["context_line"] == "entry = await repo.create("
    assert "vars" not in scrubbed["threads"]["values"][0]["stacktrace"]["frames"][0]


def test_user_context_keeps_only_the_pseudonymous_id() -> None:
    event = {
        "user": {"id": "u-123", "email": EMAIL, "ip_address": "10.0.0.1", "username": SENTINEL}
    }
    scrubbed = scrub_sentry_event(event, hint=None)
    assert scrubbed["user"] == {"id": "u-123"}


def test_the_hint_never_enters_the_event() -> None:
    """The hint carries the raw exception; nothing from it may be appended."""
    hint: dict[str, Any] = {"exc_info": RuntimeError(SENTINEL)}
    scrubbed = scrub_sentry_event({"level": "error"}, hint=hint)
    assert event_without_sentinel(scrubbed)


# --- init behaviour ------------------------------------------------------------


def test_init_without_a_dsn_is_a_noop(settings: Settings) -> None:
    assert init_sentry(settings) is False


# --- end to end through the real app ------------------------------------------


async def test_a_500_with_a_sentinel_body_ships_no_sentinel(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full path: request with a sentinel in body and query → 500 →
    before_send → the transport sees an event with neither, and no PII.

    The transport is swapped in before any request, so nothing ever leaves
    for the network and the captured events are exactly what Sentry would
    have shipped.
    """
    from app.db.repos.journals import JournalRepository

    async def explode(*args: Any, **kwargs: Any) -> Any:
        # Deliberately PII-shaped (the redactor's job), but never echoes the
        # sentinel: echoing request content in exception messages violates the
        # repo's own error-message rule, and the scrubber's contract is about
        # what the REQUEST carried.
        raise RuntimeError(f"storage failed while saving {EMAIL} / {PHONE}")

    monkeypatch.setattr(JournalRepository, "create", explode)

    settings = Settings(
        _env_file=None,
        app_env="test",
        secret_key="x" * 64,
        field_encryption_key=crypto.FernetCipher.generate_key(),
        allowed_origins="http://testserver",
        database_url=f"sqlite:///{tmp_path}/sentinel.db",
        sentry_dsn=TEST_DSN,
    )
    from app.db.session import build_database
    from app.models import Base

    database = build_database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = create_app(settings, database=database)

    # create_app initialised Sentry with the settings DSN; swap the HTTP
    # transport for a capturing one BEFORE any request.
    client = sentry_sdk.get_client()
    assert client.is_active()
    capturing = CapturingTransport()
    client.transport = capturing
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as ac:
            guest = await ac.post("/api/v1/auth/guest")
            assert guest.status_code == 201, guest.text
            headers = {"Authorization": f"Bearer {guest.json()['access_token']}"}
            granted = await ac.post(
                "/api/v1/consent",
                headers=headers,
                json={
                    "grants": [
                        {"kind": "terms", "version": "2026-10-01", "granted": True},
                        {"kind": "privacy", "version": "2026-10-01", "granted": True},
                    ]
                },
            )
            assert granted.status_code == 201, granted.text
            crashed = await ac.post(
                "/api/v1/journal?draft=" + SENTINEL,
                headers=headers,
                json={"title": SENTINEL, "body": f"{SENTINEL} and ordinary words"},
            )
            assert crashed.status_code == 500
            # The client-facing envelope stays curated too.
            assert SENTINEL not in crashed.text
        await database.dispose()
        events = capturing.events
    finally:
        sentry_sdk.init()

    assert events, f"Sentry captured nothing: {events!r}"[:400]
    for event in events:
        serialized = json.dumps(event, default=str)
        assert SENTINEL not in serialized, f"sentinel shipped: {serialized[:300]}"
        assert EMAIL not in serialized
        assert PHONE not in serialized
        # The request context was reduced, not merely redacted.
        request = event.get("request") or {}
        assert "data" not in request
        assert not request.get("query_string")
