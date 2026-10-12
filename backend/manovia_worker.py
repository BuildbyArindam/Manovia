"""The Uvicorn worker class production Gunicorn runs.

Day 27. A thin subclass of ``uvicorn_worker.UvicornWorker`` (the maintained
fork of the deprecated ``uvicorn.workers`` module) with one behavioural
change: the per-request HTTP access log is disabled at the worker level.

Why it matters: uvicorn's access line prints the full request target,
including the query string. Query strings can carry user text (a mis-pasted
journal title, a crisis phrase in a search box), and AGENTS.md rule 5 forbids
raw message text in logs at INFO or above. The application's own request line
— method, path template, status code, request id, no query string — is
emitted by ``RequestIDMiddleware`` in ``app.core.logging`` whether the server
logs access lines or not, so nothing observability needs is lost here.
"""

from __future__ import annotations

from typing import Any, ClassVar

from uvicorn_worker import UvicornWorker


class AccessLogFreeUvicornWorker(UvicornWorker):
    """UvicornWorker with ``access_log=False`` and no identifying server header."""

    CONFIG_KWARGS: ClassVar[dict[str, Any]] = {
        "loop": "auto",
        "http": "auto",
        "access_log": False,
        "server_header": False,
    }
