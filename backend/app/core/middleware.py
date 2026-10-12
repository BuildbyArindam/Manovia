"""Request middleware: security headers, rate limiting, and the Day 22 guards.

Three additions on Day 22, each one a small, independent ASGI layer so a
failure in one cannot silently disable another:

* :class:`RequestBodyLimitMiddleware` — a hard ceiling on the bytes a request
  may carry, answered with a curated ``413`` before the app ever parses it.
* :class:`RequestTimeoutMiddleware` — a total budget per request, so a slow
  dependency cannot hold a worker until the client gives up.
* :class:`OriginGuardMiddleware` — a CSRF defence in depth. Every state-changing
  request that carries an ``Origin`` must be same-origin or on
  ``ALLOWED_ORIGINS``; a request with no ``Origin`` (a script, a curl call) is
  allowed because Manovia never authenticates from a cookie, so there is no
  ambient authority for a cross-site page to borrow.
"""

import asyncio
import contextlib
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response
from app.core.ratelimit import RateLimiter

# Conservative defaults for a JSON API: no content sniffing, no framing, no
# referrer leakage, no resource loading, and no caching of responses.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    # The API serves JSON only: say so, and forbid plugins/capabilities a
    # browser might otherwise offer a document served from this origin.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "X-Permitted-Cross-Domain-Policies": "none",
}

#: Methods that cannot change state, so a cross-site caller is not a threat.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class SecurityHeadersMiddleware:
    """Adds a conservative set of security headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class RateLimitMiddleware:
    """Sliding-window request budgets per client IP: strict on the auth paths.

    Paths under ``/api/v1/auth`` share the strict ``auth_limiter`` (credential
    stuffing is the threat); the rest of ``/api/`` shares the moderate
    ``global_limiter``. Everything outside ``/api/`` (docs, OpenAPI schema) is
    not limited. Rejections are the standard 429 envelope with ``Retry-After``.

    Mounted *inside* :class:`SecurityHeadersMiddleware` so even the 429 carries
    the security headers, and inside the request-ID middleware so the envelope
    has a request ID.
    """

    AUTH_PREFIX = "/api/v1/auth"

    def __init__(
        self,
        app: ASGIApp,
        *,
        auth_limiter: RateLimiter,
        global_limiter: RateLimiter,
        enabled: bool = True,
    ) -> None:
        self.app = app
        self.auth_limiter = auth_limiter
        self.global_limiter = global_limiter
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith(self.AUTH_PREFIX):
            limiter, name = self.auth_limiter, "auth"
        elif path.startswith("/api/"):
            limiter, name = self.global_limiter, "global"
        else:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        key = f"{name}:{client[0] if client else 'unknown'}"
        result = limiter.hit(key)
        if result.allowed:
            await self.app(scope, receive, send)
            return

        state = scope.get("state") or {}
        response = error_response(
            status_code=429,
            code="rate_limited",
            message="Too many requests. Please slow down.",
            request_id=str(state.get("request_id", "unknown")),
            headers={"Retry-After": str(result.retry_after)},
        )
        await response(scope, receive, send)


def _int_or_none(value: str | None) -> int | None:
    """Parse a header value that should be a non-negative integer."""
    if value is None or not value.strip().isdigit():
        return None
    return int(value)


class RequestBodyLimitMiddleware:
    """Reject a request body larger than ``max_bytes`` with a curated 413.

    The body is read and replayed rather than streamed, which is correct here
    and only here: every endpoint takes a small JSON document (the largest
    legitimate one is a journal entry), and buffering is what makes the limit
    enforceable *before* a parser allocates memory for it. Uploads would need a
    streaming variant — there are none.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int, enabled: bool = True) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")
        self.app = app
        self.max_bytes = max_bytes
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _int_or_none(Headers(scope=scope).get("content-length"))
        if declared is not None and declared > self.max_bytes:
            await self._too_large(scope, receive, send)
            return

        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.disconnect":
                break
            total += len(message.get("body", b""))
            if total > self.max_bytes:
                await self._too_large(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        pending = list(buffered)

        async def replay() -> Message:
            if pending:
                return pending.pop(0)
            return await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = scope.get("state") or {}
        response = error_response(
            status_code=413,
            code="payload_too_large",
            message=f"Request body is larger than {self.max_bytes} bytes.",
            request_id=str(state.get("request_id", "unknown")),
        )
        await response(scope, receive, send)


class RequestTimeoutMiddleware:
    """Give every request a total budget, and answer 503 when it is exhausted.

    ``exempt_suffixes`` exists for Server-Sent Events: a stream legitimately
    stays open while the model writes, and its own deadline lives inside the
    LLM layer (``LLM_TIMEOUT_SECONDS``). Cutting a live stream at the generic
    request budget would turn "a slow reply" into "a broken reply".

    If the response has already started when the budget expires, the inner task
    is cancelled and the connection is left to end — a truncated stream is the
    honest outcome, and no second response can be sent.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        timeout_seconds: float,
        exempt_suffixes: tuple[str, ...] = (),
        enabled: bool = True,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.app = app
        self.timeout_seconds = timeout_seconds
        self.exempt_suffixes = exempt_suffixes
        self.enabled = enabled

    def _exempt(self, path: str) -> bool:
        return any(path.endswith(suffix) for suffix in self.exempt_suffixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] != "http" or self._exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        started = False

        async def watch(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        task = asyncio.ensure_future(self.app(scope, receive, watch))
        try:
            await asyncio.wait_for(asyncio.shield(task), self.timeout_seconds)
        except TimeoutError:
            if started:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                return
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            state = scope.get("state") or {}
            response = error_response(
                status_code=503,
                code="request_timeout",
                message="This took longer than we allow. Please try again.",
                request_id=str(state.get("request_id", "unknown")),
            )
            await response(scope, receive, send)


class OriginGuardMiddleware:
    """Reject cross-origin state-changing requests (CSRF defence in depth).

    Manovia authenticates with a bearer token, never a cookie, so a cross-site
    form post carries no credentials and CSRF is already structurally hard.
    This layer makes that property *checked* rather than assumed: a browser
    always attaches ``Origin`` to an unsafe cross-origin request, and an origin
    that is neither same-origin nor on ``ALLOWED_ORIGINS`` is refused with the
    standard envelope before any endpoint runs.

    A missing ``Origin`` is allowed on purpose — command-line clients, curl in
    the smoke script and the test suite send none, and they must present a
    token explicitly to do anything.
    """

    def __init__(
        self, app: ASGIApp, *, allowed_origins: tuple[str, ...], enabled: bool = True
    ) -> None:
        self.app = app
        self.allowed_origins = tuple(origin.rstrip("/") for origin in allowed_origins if origin)
        self.enabled = enabled

    def _is_same_origin(self, origin: str, scope: Scope) -> bool:
        host = Headers(scope=scope).get("host")
        if not host:
            return False
        try:
            parts = urlsplit(origin)
        except ValueError:
            return False
        return bool(parts.netloc) and parts.netloc == host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("method", "GET").upper() in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        origin = Headers(scope=scope).get("origin")
        if origin is None:
            # No ambient authority, so nothing for a third party to borrow.
            await self.app(scope, receive, send)
            return

        # "null" is the opaque origin of a sandboxed frame or a data: URL; it is
        # never same-origin with anything and is never on the allow-list.
        if origin.rstrip("/") in self.allowed_origins or self._is_same_origin(origin, scope):
            await self.app(scope, receive, send)
            return

        state = scope.get("state") or {}
        response = error_response(
            status_code=403,
            code="origin_not_allowed",
            message="This request came from an origin Manovia does not accept.",
            request_id=str(state.get("request_id", "unknown")),
        )
        await response(scope, receive, send)
