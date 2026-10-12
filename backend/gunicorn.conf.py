"""Gunicorn configuration for the production Manovia API.

Day 27: the production process model is Gunicorn managing Uvicorn worker
processes (via ``uvicorn_worker.UvicornWorker``, the maintained fork of
``uvicorn.workers`` — the module shipped inside uvicorn is deprecated).
Gunicorn owns process lifecycle (boot, heartbeat, graceful restart); each
worker runs the same ASGI app as ``make dev``.

Why a config file and not a pile of CLI flags: every value here is a
deployment decision that belongs in review, and the container CMD (see
``backend/Dockerfile``) stays a two-word invocation that cannot drift from
what is documented.

Privacy notes carried over from the single-process setup:

* the HTTP access log is OFF — uvicorn's access line prints the full request
  target including the query string, which may carry user text (AGENTS.md
  rule 5). The app's own structlog request line (path, method, status — no
  query) is emitted by ``RequestIDMiddleware`` regardless of server;
* gunicorn's error log (stderr) carries lifecycle messages and tracebacks of
  infrastructure failures only; request data never enters it.

Sizing: ``GUNICORN_WORKERS`` defaults to 2 because the reference deployment
(one small Fly shared-CPU VM next to a managed Postgres) is concurrency-,
not CPU-bound: each worker spends its time waiting on the database and the
LLM. Scale vertically first (Fly vm sizes), then horizontally; the in-memory
rate limiter and ephemeral chat store are per process, so more workers also
means more independent budgets — see the runbook before going past a handful.
"""

from __future__ import annotations

import os

# --- Binding -----------------------------------------------------------------
# The platform proxies TLS and forwards to this port (Fly's proxy, a local
# reverse proxy, ...). Never expose gunicorn directly to the internet.
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# --- Workers -----------------------------------------------------------------
worker_class = "manovia_worker.AccessLogFreeUvicornWorker"
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
# Preload must stay OFF: create_app() builds the async SQLAlchemy engine and
# the per-process caches at import time. Forking after that would share one
# connection pool across processes — the classic fork-safety bug — and would
# also make every worker share one Settings cache. Validate-in-master is not
# worth it: each worker validates settings itself at import and dies loudly.
preload_app = False
# Recycle workers after a large number of requests so slow leaks (if any ever
# appear) cannot accumulate forever. Gunicorn drains the worker gracefully.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "10000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "500"))

# --- Timeouts and graceful shutdown ------------------------------------------
# Worker heartbeat timeout. Must comfortably exceed the longest request: the
# app's own RequestTimeoutMiddleware cuts non-stream requests at
# REQUEST_TIMEOUT_SECONDS (30 s by default) and exempts SSE /stream routes,
# whose writers keep the event loop (and the gunicorn heartbeat) alive.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
# On SIGTERM (deploy, scale-down, `fly machine restart`) gunicorn stops
# accepting, lets in-flight requests finish, and force-kills stragglers after
# this many seconds. The app's lifespan shutdown (engine dispose, LLM client
# close) runs inside that window.
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
# Seconds a connection may sit idle between requests before closing.
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# --- Logging -----------------------------------------------------------------
# Privacy: no HTTP access log at all (see module docstring). Lifecycle and
# error messages go to stderr, which the platform captures.
accesslog = None
errorlog = "-"
# Gunicorn's own log lines are plain text (no user content by construction).
capture_output = True
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "warning").lower()

# --- Process identity --------------------------------------------------------
proc_name = "manovia-api"
# Trust X-Forwarded-For only from the platform proxy (Fly's private network).
# The env var, not this attribute, is what uvicorn's worker reads.
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
