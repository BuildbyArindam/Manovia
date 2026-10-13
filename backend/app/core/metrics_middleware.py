"""Pure ASGI middleware that records request rate, latency and in-flight counts.

Day 27. Sits just inside ``RequestIDMiddleware`` so every request — including
error responses and rate-limited 429s — is counted, and so the timing covers
the full middleware stack. The path label is always the route template (see
:mod:`app.core.metrics`): the raw request path is never exported.

The template is read *after* the inner app runs, because that is when Starlette
has written ``scope["route"]`` — routing decides the label, not the client. The
in-flight gauge is keyed by method only for the same reason: at request start
no route is known yet, and guessing would mean a label value derived from the
request path.
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import get_metrics, path_template_for_scope

_STATUS_CODE_START: int = 200
_STATUS_CODE_END: int = 599


class MetricsMiddleware:
    """Count and time every HTTP request against its route template."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "-"))
        metrics = get_metrics()
        started = time.perf_counter()
        status: int | None = None
        metrics.start_request(method)

        async def record_on_start(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, record_on_start)
        finally:
            duration = time.perf_counter() - started
            metrics.finish_request(method)
            # scope["route"] exists now (the inner app ran the router). A
            # closed status range keeps a malformed status from becoming a
            # label; responses that never started are counted by nothing.
            template = path_template_for_scope(scope)
            if status is not None and _STATUS_CODE_START <= status <= _STATUS_CODE_END:
                metrics.observe_request(method, template, status, duration)
