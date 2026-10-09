"""End-to-end proof that user text never reaches the structured logs.

Day 4 extends the proof to credentials: passwords and bearer tokens are as
forbidden in the logs as message text (AGENTS.md safety rule 5 plus the auth
requirements).
"""

import io
import json

import httpx
import structlog

from app.core.logging import configure_logging

E2E_SENTINEL = "E2E-USER-TEXT-SENTINEL-9d2c7b"
E2E_PASSWORD = "E2E-PASSWORD-SENTINEL-4f8a1c"
E2E_TOKEN = "E2E-TOKEN-SENTINEL-7b3e9d"


async def test_user_text_in_requests_never_appears_in_logs(client: httpx.AsyncClient) -> None:
    buffer = io.StringIO()
    configure_logging()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(buffer))
    try:
        response = await client.get(
            "/api/v1/health",
            params={
                "note": E2E_SENTINEL,
                "message_text": E2E_SENTINEL,
                "content": E2E_SENTINEL,
                "body": E2E_SENTINEL,
            },
        )
        assert response.status_code == 200
        post = await client.post(
            "/api/v1/does-not-exist",
            content=E2E_SENTINEL,
            headers={"content-type": "text/plain"},
        )
        assert post.status_code == 404
    finally:
        configure_logging()  # restore the stdout logger factory

    output = buffer.getvalue()
    assert E2E_SENTINEL not in output
    records = [json.loads(line) for line in output.strip().splitlines()]
    request_logs = [record for record in records if record.get("event") == "http_request"]
    assert request_logs, "expected request logs to be emitted"
    assert all(record.get("request_id") for record in request_logs)


async def test_passwords_and_tokens_never_appear_in_logs(client: httpx.AsyncClient) -> None:
    """A full signup/login cycle with sentinel credentials: logs stay clean."""
    buffer = io.StringIO()
    configure_logging()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(buffer))
    try:
        registered = await client.post(
            "/api/v1/auth/register",
            json={"email": "logs@example.test", "password": E2E_PASSWORD},
        )
        assert registered.status_code == 201
        tokens = registered.json()

        logged_in = await client.post(
            "/api/v1/auth/login",
            json={"email": "logs@example.test", "password": E2E_PASSWORD},
            headers={"Authorization": f"Bearer {E2E_TOKEN}"},
        )
        assert logged_in.status_code == 200

        refreshed = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refreshed.status_code == 200

        await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    finally:
        configure_logging()  # restore the stdout logger factory

    output = buffer.getvalue()
    assert E2E_PASSWORD not in output, "a password reached the logs"
    assert E2E_TOKEN not in output, "a bearer token reached the logs"
    assert tokens["access_token"] not in output, "an issued access token reached the logs"
    assert tokens["refresh_token"] not in output, "an issued refresh token reached the logs"
    # The requests themselves are still logged — just without credentials.
    records = [json.loads(line) for line in output.strip().splitlines()]
    paths = [record.get("path") for record in records if record.get("event") == "http_request"]
    assert "/api/v1/auth/register" in paths
    assert "/api/v1/auth/login" in paths


async def test_sensitive_field_names_are_dropped_from_any_log_record() -> None:
    """Defence in depth: even a careless logger call loses credential fields."""
    from app.core.logging import drop_sensitive_fields

    event = {
        "event": "sign_in",
        "password": E2E_PASSWORD,
        "refresh_token": E2E_TOKEN,
        "nested": {"access_token": E2E_TOKEN, "keep": "value"},
        "keep": "value",
    }
    cleaned = drop_sensitive_fields(None, "info", event)
    assert cleaned == {"event": "sign_in", "nested": {"keep": "value"}, "keep": "value"}
