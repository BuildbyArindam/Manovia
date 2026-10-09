# ADR 0002: Backend skeleton — configuration, logging, and error conventions

- **Status:** Accepted (Day 2 backend skeleton)
- **Date:** 2026-10-09

## Context

Day 2 introduces the FastAPI backend skeleton. The agent operating rules require environment-only configuration, no raw message text in INFO-level logs, and small tested increments. API consumers also need predictable errors, and operators need request tracing and safe defaults.

## Decisions

1. **Error envelope.** Every error response uses `{"error": {"code", "message", "request_id"}}` with curated messages. Exception details and request-validation input are never echoed to clients, because either can contain user-controlled text. The request ID is repeated as the `X-Request-ID` response header (also on the unhandled-exception path, which bypasses the middleware stack).
2. **Logging.** structlog emits one JSON object per line on stdout. A `RequestIDMiddleware` (pure ASGI) honours an incoming `X-Request-ID` or generates one, stores it on `request.state`, binds it to structlog contextvars, and logs exactly one line per request containing method, path, and status — never the query string. A `drop_sensitive_fields` processor removes any field named `message_text`, `content`, `body`, or `note` (recursively) from every event dict before rendering. Uvicorn's access log is disabled in `make dev` because it prints full request lines including query strings.
3. **Configuration.** pydantic-settings `Settings` reads process environment variables and `.env` files (`backend/.env`, then the repository-root `.env`). `SECRET_KEY` is required when `APP_ENV=production`. `ALLOWED_ORIGINS` is a comma-separated list used for CORS. `get_settings()` is cached per process; `create_app()` accepts an injected `Settings` for tests.
4. **Security headers.** Every HTTP response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, and `Cache-Control: no-store`.
5. **Testing.** Integration tests drive the app through `httpx.AsyncClient` with an ASGI transport — no network, no database, fully offline. pytest-asyncio runs in auto mode. The Day 2 skeleton keeps 100% coverage of `app/`.

## Consequences

- User text cannot reach logs through structured fields, and request logs never include query strings; the redaction is proven by unit, property (hypothesis), and end-to-end tests.
- Error responses are stable and safe to display, and every response is traceable by request ID.
- Configuration mistakes fail closed: readiness returns 503 when settings do not validate.
- Follow-ups: extend the redaction blocklist as chat fields appear; add the database readiness check; keep uvicorn access logging off (or sanitised) in deployment configuration; wire SQLAlchemy/Alembic.
