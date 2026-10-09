# Backend

FastAPI backend for Manovia. The skeleton provides environment-based
configuration, structured JSON logging with a request-ID middleware and a
log-redaction processor, a consistent error envelope, security headers, CORS,
and liveness/readiness endpoints.

## Setup

```bash
cd backend
pip install -e ".[dev]"   # or: uv sync
```

## Commands

| Command       | What it does                                          |
| ------------- | ----------------------------------------------------- |
| `make dev`    | Run the API on http://localhost:8000 (uvicorn, reload) |
| `make test`   | Run pytest with a coverage summary                    |
| `make lint`   | ruff (check + format check) and mypy                  |
| `make format` | Apply ruff formatting and safe lint fixes             |

## Layout

- `app/main.py` — application factory (`create_app()`)
- `app/core/config.py` — pydantic-settings configuration (reads `.env`)
- `app/core/logging.py` — structlog JSON logging, log redaction, request-ID middleware
- `app/core/errors.py` — error envelope `{"error": {"code", "message", "request_id"}}`
- `app/core/middleware.py` — security headers middleware
- `app/api/v1/health.py` — `GET /api/v1/health` (liveness) and `GET /api/v1/ready` (readiness)
- `tests/unit`, `tests/integration` — pytest suites (httpx `AsyncClient` over ASGI)

Configuration comes from environment variables only (see `../.env.example`).
`SECRET_KEY` is required when `APP_ENV=production`. No database models exist
yet; the SQLAlchemy/Alembic wiring and the database readiness check are planned
for the next milestone.
