"""The Day 22 request guards: body ceiling, timeout, origin check, headers.

The guards are tested both ways — through the real application (so they are
wired in the right order and the error envelope is the product's own) and as
small units over a synthetic ASGI app (so a timing test measures the middleware
and not the whole stack).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.middleware import (
    SECURITY_HEADERS,
    OriginGuardMiddleware,
    RequestBodyLimitMiddleware,
    RequestTimeoutMiddleware,
)

RATE_LIMIT_SAFE = {"Authorization": "Bearer not-a-real-token"}


def _tiny_app(*, sleep: float = 0.0, status: int = 200) -> Starlette:
    async def endpoint(request):  # type: ignore[no-untyped-def]
        if sleep:
            await asyncio.sleep(sleep)
        return JSONResponse({"status": "ok"}, status_code=status)

    return Starlette(routes=[Route("/ping", endpoint, methods=["GET", "POST"])])


def _client(app, *, base_url: str = "http://testserver") -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


# --------------------------------------------------------------------------- #
# Body ceiling                                                                  #
# --------------------------------------------------------------------------- #


async def test_body_ceiling_is_enforced_for_an_oversized_body(
    client: httpx.AsyncClient,
) -> None:
    payload = b"x" * 300_000  # over the 256 KiB default
    response = await client.post(
        "/api/v1/auth/guest", content=payload, headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.json()["error"]["request_id"]


async def test_body_ceiling_is_not_enforced_for_an_honest_body(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201


async def test_body_ceiling_counts_a_chunked_body_without_content_length() -> None:
    """A missing Content-Length must not become a hole in the ceiling.

    Driven at the ASGI layer directly: httpx would compute a Content-Length for
    us and this test is specifically about a body that arrives in chunks with
    no declared size.
    """
    inner_called = False

    async def inner_app(scope, receive, send):  # type: ignore[no-untyped-def]
        nonlocal inner_called
        inner_called = True
        message = await receive()
        assert message["body"] == b"a" * 40

    app = RequestBodyLimitMiddleware(inner_app, max_bytes=64)
    chunks = [b"a" * 40, b"b" * 40]
    consumed: list[bool] = []

    async def receive():  # type: ignore[no-untyped-def]
        if chunks:
            return {
                "type": "http.request",
                "body": chunks.pop(0),
                "more_body": bool(chunks),
            }
        return {"type": "http.disconnect"}

    sent: list[dict[str, Any]] = []

    async def send(message):  # type: ignore[no-untyped-def]
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/ping",
        "headers": [(b"content-type", b"text/plain")],
        "client": ("127.0.0.1", 1234),
        "state": {},
    }
    await app(scope, receive, send)
    assert inner_called is False
    assert sent[0]["status"] == 413
    assert consumed == []


async def test_a_body_limit_error_carries_the_security_headers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/v1/auth/guest", content=b"x" * 300_000)
    assert response.status_code == 413
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name


# --------------------------------------------------------------------------- #
# Timeout                                                                       #
# --------------------------------------------------------------------------- #


async def test_a_slow_request_is_answered_with_the_timeout_envelope() -> None:
    app = RequestTimeoutMiddleware(_tiny_app(sleep=0.5), timeout_seconds=0.05)
    async with _client(app) as ac:
        response = await ac.get("/ping")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "request_timeout"


async def test_a_request_inside_the_budget_is_untouched() -> None:
    app = RequestTimeoutMiddleware(_tiny_app(sleep=0.01), timeout_seconds=5.0)
    async with _client(app) as ac:
        response = await ac.get("/ping")
    assert response.status_code == 200


async def test_streaming_paths_are_exempt_from_the_generic_budget() -> None:
    """An SSE reply may legitimately outlive the generic request budget."""
    app = RequestTimeoutMiddleware(
        _tiny_app(sleep=0.2), timeout_seconds=0.05, exempt_suffixes=("/stream",)
    )

    async def stream_endpoint(request):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.2)
        return JSONResponse({"streamed": True})

    app = RequestTimeoutMiddleware(
        Starlette(routes=[Route("/chat/sessions/1/stream", stream_endpoint, methods=["POST"])]),
        timeout_seconds=0.05,
        exempt_suffixes=("/stream",),
    )
    async with _client(app) as ac:
        response = await ac.post("/chat/sessions/1/stream")
    assert response.status_code == 200

    # The same slow work on a normal path is still cut off.
    slow = RequestTimeoutMiddleware(_tiny_app(sleep=0.2), timeout_seconds=0.05)
    async with _client(slow) as ac:
        assert (await ac.get("/ping")).status_code == 503


# --------------------------------------------------------------------------- #
# Origin guard (CSRF defence in depth)                                          #
# --------------------------------------------------------------------------- #


async def test_a_cross_origin_write_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/guest", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


async def test_an_allowed_origin_is_accepted(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/guest", headers={"Origin": "http://testserver"})
    assert response.status_code == 201


async def test_a_request_without_an_origin_is_allowed(client: httpx.AsyncClient) -> None:
    """curl, the smoke script and the test suite send no Origin."""
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201


async def test_a_safe_method_with_a_foreign_origin_is_still_allowed(
    client: httpx.AsyncClient,
) -> None:
    """A cross-origin *read* has no ambient authority left to borrow."""
    response = await client.get(
        "/api/v1/crisis/resources", headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 200


async def test_the_opaque_null_origin_is_refused() -> None:
    """`Origin: null` (sandboxed iframe, data: URL) is never same-origin."""
    app = OriginGuardMiddleware(_tiny_app(), allowed_origins=("http://testserver",))
    async with _client(app) as ac:
        response = await ac.post("/ping", headers={"Origin": "null"})
    assert response.status_code == 403


async def test_the_origin_guard_can_be_switched_off() -> None:
    app = OriginGuardMiddleware(_tiny_app(), allowed_origins=("http://testserver",), enabled=False)
    async with _client(app) as ac:
        response = await ac.post("/ping", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


async def test_the_refusal_is_the_standard_envelope(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/guest", headers={"Origin": "https://evil.example"})
    body = response.json()
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert json.loads(response.text) == body
    assert response.headers["content-type"].startswith("application/json")


# --------------------------------------------------------------------------- #
# Headers                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
        ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
        ("Cache-Control", "no-store"),
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
        ("X-Permitted-Cross-Domain-Policies", "none"),
    ],
)
async def test_every_security_header_is_present_on_an_api_response(
    client: httpx.AsyncClient, header: str, value: str
) -> None:
    response = await client.get("/api/v1/health")
    assert response.headers.get(header) == value


async def test_an_error_response_carries_the_headers_too(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
