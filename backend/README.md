# Backend

FastAPI backend for Manovia: environment-based configuration, structured JSON
logging with request IDs and log redaction, a consistent error envelope, security
headers, CORS, liveness/readiness endpoints, and the Day 3 data layer (SQLAlchemy
2 models, Alembic migrations, async sessions, thin repositories, and field
encryption for personal text).

## Setup

```bash
cd backend
pip install -e ".[dev]"   # or: uv sync
cp ../.env.example .env   # then fill in DATABASE_URL and FIELD_ENCRYPTION_KEY
alembic upgrade head      # create the schema (or: make -C .. db-upgrade)
```

Local development works with no configuration at all: `DATABASE_URL` defaults to
`sqlite:///./manovia.db`. Writing anything personal needs `FIELD_ENCRYPTION_KEY`
(generate a throwaway one with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).

## Commands

| Command                 | What it does                                              |
| ----------------------- | --------------------------------------------------------- |
| `make dev`              | Run the API on http://localhost:8000 (uvicorn, reload)     |
| `make test`             | Run pytest with a coverage summary                         |
| `make lint`             | ruff (check + format check) and mypy                       |
| `make format`           | Apply ruff formatting and safe lint fixes                  |
| `make migrate`          | `alembic upgrade head`                                     |
| `make migrate-down`     | `alembic downgrade base`                                   |
| `make migrate-check`    | Fail if the models and the migrations have drifted         |
| `make revision m="..."` | Autogenerate the next migration from the models             |

## Layout

- `app/main.py` — application factory (`create_app()`); builds the `Database`,
  installs the field cipher, disposes the engine on shutdown
- `app/core/config.py` — pydantic-settings configuration (reads `.env`); validates
  `DATABASE_URL` and requires `SECRET_KEY` + `FIELD_ENCRYPTION_KEY` in production
- `app/core/crypto.py` — `FieldCipher` protocol + `FernetCipher`; the only code that
  turns personal text into ciphertext and back
- `app/core/logging.py` — structlog JSON logging, log redaction, request-ID middleware
- `app/core/errors.py` — error envelope `{"error": {"code", "message", "request_id"}}`
- `app/core/middleware.py` — security headers middleware
- `app/models/` — one typed SQLAlchemy 2 model per table, plus `base.py` (naming
  convention, portable column helpers) and `enums.py`
- `app/db/session.py` — async engine + session factory, SQLite/PostgreSQL URL
  handling, `PRAGMA foreign_keys`, readiness probe helper
- `app/db/repos/` — the only place statements are built; they never commit
- `app/api/deps.py` — `Depends` helpers (`get_session`, `get_database`)
- `app/api/v1/health.py` — `GET /api/v1/health` (liveness), `GET /api/v1/ready`
  (config + database)
- `alembic/` — migration environment (`env.py` reads `DATABASE_URL` through
  `Settings`) and `versions/0001_initial_schema.py`
- `tests/unit`, `tests/integration` — pytest suites; integration tests run against a
  real SQLite file, fully offline

## Schema notes

- Erasing a person is `DELETE FROM users WHERE id = ?`: every child table has
  `ON DELETE CASCADE`, and `messages` cascades from `chat_sessions`.
- Free text is never stored in the clear: `messages.content_encrypted`,
  `journal_entries.title_encrypted` / `body_encrypted`, and
  `mood_entries.note_encrypted` are ciphertext blobs with no plaintext sibling.
- `safety_events` has no textual column: ids, a risk tier, a source, a timestamp.
- `tests/unit/test_models.py` pins those invariants, and
  `tests/integration/test_migrations.py` fails if the models and the migration drift.

Configuration comes from environment variables only (see `../.env.example`).
