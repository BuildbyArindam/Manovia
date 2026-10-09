"""Request middleware: security headers and rate limiting."""

from starlette.datastructures import MutableHeaders
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
}


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
