"""End-to-end proof that user text never reaches the structured logs."""

import io
import json

import httpx
import structlog

from app.core.logging import configure_logging

E2E_SENTINEL = "E2E-USER-TEXT-SENTINEL-9d2c7b"


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
