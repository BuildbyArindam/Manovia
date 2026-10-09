"""Structured JSON logging and the request-ID middleware."""

import logging
import sys
import uuid
from collections.abc import MutableMapping
from typing import Any

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"

# Fields that may carry raw user text or credentials. They are dropped from
# every log record, including inside nested dictionaries and lists. This is
# defence in depth for the "never log raw message text" rule in AGENTS.md and
# for the Day 4 rule that passwords and tokens never reach the logs.
SENSITIVE_LOG_FIELDS: frozenset[str] = frozenset(
    {
        # Raw user text (AGENTS.md safety rule 5).
        "message_text",
        "content",
        "body",
        "note",
        # NLP request fields (Day 6): the dev endpoint accepts `text`, and a
        # future prompt surface will accept `prompt`/`query`. The endpoint logs
        # a SHA-256 fingerprint instead, but the blocklist is defence in depth
        # for any call site that forgets.
        "text",
        "prompt",
        "query",
        # Credentials and session material (Day 4).
        "password",
        "password_hash",
        "token",
        "token_hash",
        "access_token",
        "refresh_token",
        "authorization",
        "secret",
        "secret_key",
    }
)


def _strip_sensitive_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_sensitive_fields(item)
            for key, item in value.items()
            if key not in SENSITIVE_LOG_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_strip_sensitive_fields(item) for item in value]
    return value


def drop_sensitive_fields(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Structlog processor: remove any field that may carry user text.

    Drops every field named ``message_text``, ``content``, ``body`` or ``note``
    from the event dict (recursively) before the record is rendered.
    """
    stripped: dict[str, Any] = _strip_sensitive_fields(dict(event_dict))
    return stripped


def _resolve_log_level(level: str) -> int:
    resolved = getattr(logging, level.strip().upper(), None)
    return resolved if isinstance(resolved, int) else logging.INFO


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to emit one JSON object per line on stdout."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            drop_sensitive_fields,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_resolve_log_level(level)),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        # Keep reconfiguration effective for tests that capture log output.
        cache_logger_on_first_use=False,
    )


def _incoming_request_id(scope: Scope) -> str | None:
    raw = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


class RequestIDMiddleware:
    """Pure ASGI middleware that gives every request a unique request ID.

    The ID is honoured from an incoming ``X-Request-ID`` header when present,
    otherwise generated. It is returned in the ``X-Request-ID`` response
    header, stored on ``request.state`` for the error handlers, and bound to
    structlog contextvars so every log line for the request carries it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        status_code: int | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        logger = structlog.get_logger()
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            # Path only — never the query string, which may contain user text.
            logger.info(
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                status_code=status_code,
            )
            structlog.contextvars.clear_contextvars()
