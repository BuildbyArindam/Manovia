"""Unit tests for the ASGI middlewares' non-HTTP passthrough behaviour."""

from starlette.types import Message, Receive, Scope, Send

from app.core.logging import RequestIDMiddleware
from app.core.middleware import SecurityHeadersMiddleware


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _send(message: Message) -> None:
    return None


async def test_request_id_middleware_forwards_non_http_scopes() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    middleware = RequestIDMiddleware(inner)
    await middleware({"type": "lifespan"}, _receive, _send)
    assert seen == ["lifespan"]


async def test_security_headers_middleware_forwards_non_http_scopes() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    middleware = SecurityHeadersMiddleware(inner)
    await middleware({"type": "lifespan"}, _receive, _send)
    assert seen == ["lifespan"]
