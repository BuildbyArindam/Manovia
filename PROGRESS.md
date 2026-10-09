# Manovia — Progress

## Current status
Day 2 (FastAPI backend skeleton) is complete and verified. Work is on the session branch `arena/a145bc80-manovia`; the requested `day-02-backend-skeleton-fastapi` branch was not used because this session is fixed to the Arena session branch (same arrangement as Day 1). Day 1 (repository scaffold) is merged on `main`.

## Completed

### Day 1 (merged on `main`)
- Repository scaffolding: `backend/`, `frontend/`, `docs/`, `evals/`, `scripts/`, root README/LICENSE/`.env.example`/Makefile stubs/pre-commit config, `AGENTS.md`, and [ADR 0001](docs/adr/0001-monorepo-and-stack.md).
- All Makefile targets were placeholders; no application code or dependencies.

### Day 2 (branch `arena/a145bc80-manovia`)
- `backend/pyproject.toml`: Python 3.11+ package (editable install) with fastapi, uvicorn[standard], pydantic v2, pydantic-settings, sqlalchemy 2, alembic, httpx, structlog; dev extras: pytest, pytest-asyncio, pytest-cov, ruff, mypy, hypothesis.
- `app/main.py`: `create_app()` factory wiring CORS (from `ALLOWED_ORIGINS`), security headers, the request-ID middleware, and the error handlers; module-level `app` for uvicorn.
- `app/core/config.py`: pydantic-settings `Settings` reading `.env` (`backend/.env` or repo-root `.env`) and the environment; `SECRET_KEY` required when `APP_ENV=production`; `ALLOWED_ORIGINS` parsed for CORS; cached `get_settings()`.
- `app/core/logging.py`: structlog JSON logging on stdout; `drop_sensitive_fields` processor drops any field named `message_text`, `content`, `body` or `note` (recursively, defence in depth for AGENTS.md rule 5); `RequestIDMiddleware` (pure ASGI) honours/generates `X-Request-ID`, stores it on `request.state`, binds it to contextvars, and logs one line per request (path only, never the query string).
- `app/core/middleware.py`: `SecurityHeadersMiddleware` — `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, `Cache-Control: no-store`.
- `app/core/errors.py`: consistent error envelope `{"error": {"code", "message", "request_id"}}` for HTTP/validation/unhandled errors; curated messages only — exception details and validation input are never echoed; the request ID is also set as a response header (covers the unhandled-exception path, which bypasses the middleware stack).
- `app/api/v1/health.py`: `GET /api/v1/health` (liveness) and `GET /api/v1/ready` (readiness — fails closed with 503 when the configuration does not load; DB check deferred to the database milestone).
- Tests: 34 tests (unit + integration) driving the app through `httpx.AsyncClient` over ASGI (fully offline), including a hypothesis property test for redaction and an end-to-end test proving user text in query params/bodies never appears in logs. Coverage: 100% of `app/` (173 statements).
- Tooling: `backend/Makefile` with real `dev`/`test`/`lint`/`format` targets; root `Makefile` `dev`/`test`/`lint`/`format` delegate to `backend/`; `eval`/`up`/`down` remain placeholders. `.env.example` gained `LOG_LEVEL`; `.gitignore` ignores `.hypothesis/`.
- Commits on the branch: `3d048b9` (tooling), `4ce14a4` (core), `687742c` (API + tests), `6bac7bb` (uvicorn access-log privacy fix).

## Verification (Day 2 — real command output)
- `cd backend && pip install -e ".[dev]"` — succeeded. Resolved: fastapi 0.143.0, starlette 1.7.0, pydantic 2.14.0, pydantic-settings 2.15.0, structlog 26.1.0, sqlalchemy 2.1.4, alembic 1.20.0, httpx 0.28.1, uvicorn 0.54.0, pytest 9.1.1, pytest-asyncio 1.4.0, pytest-cov 7.1.0, ruff 0.16.10, mypy 2.4.0, hypothesis 6.168.5.
- `make lint` — `ruff check` clean; `ruff format --check` clean (24 files); `mypy app tests` (strict) — "Success: no issues found in 23 source files".
- `make test` — 34 passed; coverage TOTAL 173 stmts, 0 miss, 100%.
- Live server (`make dev`, uvicorn on 0.0.0.0:8000, then stopped):
  - `curl -s localhost:8000/api/v1/health` → `{"status":"ok"}`
  - `curl -s -i localhost:8000/api/v1/ready` → 200; body `{"status":"ready","checks":{"config":"ok"},"app_env":"development"}`; response headers include `x-request-id`, `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy: no-referrer`, `content-security-policy: default-src 'none'; frame-ancestors 'none'`, `cache-control: no-store`.
  - `curl -s -i localhost:8000/api/v1/does-not-exist` → 404; body `{"error":{"code":"not_found","message":"Not found","request_id":"<matches x-request-id header>"}}`.
  - `curl -s localhost:8000/openapi.json | head -c 400` → valid OpenAPI 3.1.0 document (`"title":"Manovia API"`).
  - Live redaction proof: requests carrying `LIVE-SENTINEL-abc123` in query params (`note`, `message_text`, `content`, `body`) and in a POST body produced structlog lines with path only; the sentinel appeared nowhere in the server logs after uvicorn's access log was disabled.
- Log-redaction tests (5, all passing): `test_drop_sensitive_fields_removes_blocked_fields`, `test_drop_sensitive_fields_recurses_into_nested_structures`, `test_rendered_json_logs_never_contain_user_text` (key assertion: `assert USER_TEXT_SENTINEL not in output`), `test_redaction_holds_for_arbitrary_event_dicts` (hypothesis property), `test_user_text_in_requests_never_appears_in_logs` (end-to-end; key assertion: `assert E2E_SENTINEL not in output`).
- Root-level `make lint` / `make test` pass via delegation to `backend/`. Frontend: no code yet (README only) — nothing to lint or test there.

## Decisions (ADR index)
- [0001 — Monorepo and stack](docs/adr/0001-monorepo-and-stack.md): FastAPI + Pydantic v2, SQLAlchemy 2 + Alembic + PostgreSQL, React + Vite + TypeScript + Tailwind, provider-abstracted LLM integrations with offline fakes, Docker for local orchestration.
- [0002 — Backend skeleton: configuration, logging, and error conventions](docs/adr/0002-backend-skeleton-conventions.md): error envelope, structlog JSON logging with request IDs and field redaction, env-only settings with the production `SECRET_KEY` rule, security headers, offline ASGI-transport testing.

## Known issues
- This sandbox's pip is PEP 668 externally-managed (Debian image); `pip config set global.break-system-packages true` was set once so the plain `pip install -e ".[dev]"` command works here. Not a repository issue; on a normal machine or venv the plain command works as-is.
- SQLAlchemy/Alembic are installed but not wired yet: no engine, session factory, models, or migrations. The readiness DB check is deferred by design.
- Uvicorn's access log is disabled in `make dev` for privacy (it prints full request lines including query strings). Deployment uvicorn configuration must keep access logs off or sanitise them; no deployment config exists yet.
- `pre-commit` is not installed in the sandbox; the configured hooks (ruff, ruff-format, gitleaks) were not run.
- Frontend, evals, CI workflows, and Docker orchestration remain placeholders.
- The 49-day plan document is still unavailable; the Day 3 steps below are inferred from the day-plan messages.

## Parking lot
- Extend the log-redaction blocklist (e.g. `text`, `prompt`, `raw_input`) once chat endpoints exist.
- Revisit echoing `HTTPException.detail` for server-controlled errors (currently never echoed).
- Database layer: engine/session factory, Alembic environment + first migration, DB check in `/api/v1/ready`, `DATABASE_URL` validation.
- LLM provider interface with offline fakes (per ADR 0001).
- Helpline content module (`app/content/helplines.json` with `last_verified`) behind a read-only endpoint.
- Frontend scaffold (React + Vite + TypeScript + Tailwind), evals harness, CI workflows, Docker compose.
- Restore the legacy `chatbot-1/` source under `legacy/` when supplied; add the 49-day plan to `docs/plan/` when available.

## Next steps (Day 3 — first three)
1. Wire the SQLAlchemy 2 engine/session factory and the Alembic environment with a first (empty) migration, configured from `DATABASE_URL`.
2. Add the database check to `GET /api/v1/ready` (fail closed with 503 when the DB is unreachable) and validate `DATABASE_URL` in `Settings`.
3. Add the helpline content module (`app/content/helplines.json` with `last_verified`) behind a read-only endpoint with unit + integration tests.
