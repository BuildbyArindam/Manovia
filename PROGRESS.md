# Manovia — Progress

## Current status

Day 3 (database models and migrations) is complete and verified. Work is on the session branch
`arena/ff090752-manovia`; the requested `day-03-database-models-migrations` branch was not used
because this session is fixed to the Arena session branch (same arrangement as Days 1-2). Day 2
(FastAPI backend skeleton) is merged on `main`.

## Completed

### Day 1 (merged on `main`)

- Repository scaffolding: `backend/`, `frontend/`, `docs/`, `evals/`, `scripts/`, root README/LICENSE/`.env.example`/Makefile stubs/pre-commit config, `AGENTS.md`, and [ADR 0001](docs/adr/0001-monorepo-and-stack.md).
- All Makefile targets were placeholders; no application code or dependencies.

### Day 2 (merged on `main`)

- `backend/pyproject.toml`: Python 3.11+ package (editable install) with fastapi, uvicorn[standard], pydantic v2, pydantic-settings, sqlalchemy 2, alembic, httpx, structlog; dev extras: pytest, pytest-asyncio, pytest-cov, ruff, mypy, hypothesis.
- `app/main.py`: `create_app()` factory wiring CORS (from `ALLOWED_ORIGINS`), security headers, the request-ID middleware, and the error handlers; module-level `app` for uvicorn.
- `app/core/config.py`: pydantic-settings `Settings`; `SECRET_KEY` required when `APP_ENV=production`; `ALLOWED_ORIGINS` parsed for CORS; cached `get_settings()`.
- `app/core/logging.py`: structlog JSON logging; `drop_sensitive_fields` removes `message_text`, `content`, `body`, `note` recursively; `RequestIDMiddleware` (path-only request logs).
- `app/core/middleware.py` / `app/core/errors.py`: security headers; the `{"error": {"code","message","request_id"}}` envelope with curated messages.
- `app/api/v1/health.py`: liveness + readiness (config only, DB deferred to Day 3).
- Tests: 34 tests, 100% coverage of `app/`.

### Day 3 (branch `arena/ff090752-manovia`) — data layer

**Models** (`backend/app/models/`, typed SQLAlchemy 2 declarations, one module per table):

- `User` — `id` uuid pk, `email` nullable unique, `password_hash` nullable, `is_anonymous`, `language` (BCP 47), `region` (ISO 3166-1 alpha-2), `created_at`, `deleted_at` nullable.
- `Consent` — `kind` (`terms`, `privacy`, `ai_disclosure`, `store_chat`), `version`, `granted`, `created_at`; append-only, so a withdrawal is a new row (no unique constraint on `(user_id, kind, version)` — the newest row wins).
- `ChatSession` — `user_id`, `created_at`, `ended_at`.
- `Message` — `session_id`, `role` (`user`/`assistant`/`system`), `content_encrypted` (LargeBinary), `risk_level` smallint 0-3, `emotion` nullable, `created_at`. **No plaintext column exists.**
- `MoodEntry` — `valence` 1-5, `energy` 1-5 (CHECK), `emotions` json list, `factors` json object, `note_encrypted` nullable, `created_at`.
- `JournalEntry` — `title_encrypted`, `body_encrypted`, `sentiment` float nullable (CHECK in `[-1,1]`), `created_at`.
- `AssessmentResult` — `instrument` (`phq9`/`gad7`), `answers` json, `total`, `band`, `created_at`.
- `SafetyEvent` — `user_id` nullable, `session_id` nullable, `risk_level`, `source` (`rules`/`ml`/`llm_output`), `created_at`. **Metadata only**: an automated test whitelists its column types so a future text column fails CI.
- Portable by construction: `sa.Uuid` ids, `LargeBinary` ciphertext, `SmallInteger` scales, `JSON().with_variant(JSONB, "postgresql")`, enum-ish columns as `VARCHAR` + named `CHECK` (no native PostgreSQL enum types), `MetaData(naming_convention=...)` so every constraint is named on both backends, and `(user_id, created_at)` indexes everywhere (`(session_id, created_at)` on `messages`).
- `ON DELETE CASCADE` from `users` to every child table and from `chat_sessions` to `messages`/`safety_events`; relationships use `passive_deletes=True`; `app/db/session.py` sets `PRAGMA foreign_keys=ON` on every SQLite connection so the cascade is real in development too.

**Encryption** (`app/core/crypto.py`): `FieldCipher` protocol + `FernetCipher` reading `FIELD_ENCRYPTION_KEY` (validated on construction), `configure_cipher()` installed by `create_app()`, `encrypt`/`decrypt` (+ `*_optional`), `DecryptError` when a blob was not produced by the configured key. Personal text is encrypted at the repository boundary and decrypted only through named calls (`ChatRepository.message_text`, `JournalRepository.title/body`, `MoodRepository.note`). Missing key ⇒ writes refuse to happen; `Settings` refuses to start in production without one. Per-user data keys are a later milestone (see ADR 0003 §5).

**Migrations**: `backend/alembic.ini` + `backend/alembic/env.py` (async engine, URL always resolved through `Settings`, `render_as_batch=True`, `compare_type=True`, `compare_server_default` off) and `alembic/versions/0001_initial_schema.py` (autogenerate output, hand-reviewed; explicit downgrade).

**Session + repositories**: `app/db/session.py` (`async_database_url` driver upgrade for sqlite/postgres, `create_db_engine`, `async_sessionmaker(expire_on_commit=False)`, `Database` with `check()`/`dispose()`/`describe()`, `check_database()`), `app/db/repos/{users,consents,chats,moods,journals,assessments,safety}.py` (statement-building only, `flush` never `commit`), `app/api/deps.py` (`Depends(get_session)`/`get_database`).

**API + scripts**: `GET /api/v1/ready` now returns `{"config":"ok","database":"ok"}` and fails closed with 503 + the standard error envelope when the database does not answer (never naming the DSN). `scripts/seed_demo_data.py` creates one demo user with a row in every table (4 consents, a closed session with 4 messages incl. 2 crisis-tier, 2 safety events, a mood check-in, a journal entry, a PHQ-9 result), refuses to run without a key or against an unmigrated database, is idempotent, and `--fresh` rebuilds the demo user through the cascade. `make dev/test/lint/format` plus new `migrate`, `migrate-down`, `migrate-check`, `revision` targets (root: `db-upgrade`, `db-downgrade`, `db-check`, `seed`).

**Tests**: 142 tests, 100% coverage of `app/` (753 statements) — 34 carried over from Day 2, 108 new: migration round trip + drift, cascade delete (including the two cases that must not cascade), encrypted-at-rest (file-level assertions), schema constraints, every repository query, the session dependency, the app lifecycle, the seed script, and the crypto interface.

## Verification (Day 3 — real command output)

- **Deps** — `cd backend && pip install -e ".[dev]"`: resolves fastapi 0.143.0, starlette 1.7.0, pydantic 2.14.0, sqlalchemy 2.1.4 (+greenlet 3.5.6), alembic 1.20.0, aiosqlite 0.22.1, asyncpg 0.32.0, cryptography 50.0.2, pytest 9.1.1, ruff 0.16.10, mypy 2.4.0, hypothesis 6.168.5.
- **1. Migration round trip on a fresh DB** — `rm -f manovia.db`, then `alembic upgrade head` → `Running upgrade -> 0001, initial schema`; `alembic downgrade base` → `Running downgrade 0001 -> `; `alembic upgrade head` again; `alembic current` → `0001 (head)`. All three exit 0; 8 tables + `alembic_version` in the 122 KB file.
- **2. Drift** — `cd backend && alembic check` → `No new upgrade operations detected.` (exit 0). The same comparison runs in CI as `test_migration_and_models_do_not_drift` and `test_autogenerate_diff_is_empty` (`compare_metadata(...) == []`).
- **3. `make test`** — 142 passed, coverage TOTAL 753 stmts / 0 miss / 100 %. New files named in the run: `test_migrations.py` (5: up/down/up, idempotent head, `alembic check`, empty autogenerate diff, single root revision), `test_cascade_delete.py` (7: `hard_delete` empties all 7 child tables, ORM delete, session-level cascade, non-cascading cases, soft delete), `test_encryption_at_rest.py` (6: whole-file plaintext search, per-column blob checks, decrypt round trip through repos, wrong key raises, "only the words are encrypted"), plus `test_constraints.py` (8), `test_repos.py` (10), `test_db_deps.py` (3), `test_app_lifecycle.py` (3), `test_seed_script.py` (8), `test_crypto.py` (14), `test_models.py` (21), `test_session.py` (11), and 12 more in the two updated files (`test_config.py` 6 → 14, `test_health.py` 3 → 7).
- **4. Seed + raw read** — `python3 scripts/seed_demo_data.py` printed `demo user: 52f8f53c-… (demo@manovia.local / manovia-demo)` with `consents: 4, messages: 4, safety_events: 2, …`. Then `select id, hex(substr(content_encrypted,1,16)) from messages limit 3;` → e.g. `('b5b17c13…', '6741414141414271794A6A4242445331')`; cast to text that is `gAAAAABqyJjBBDS1…` (a Fernet token, not the input). A byte search over the whole file for `not slept properly`, `better off without me`, `still wired`, `stopped multitasking` → all "not present". Decrypting with the `.env` key returns the original sentences, so it is encryption and not an encoding.
- **5. Live readiness** — `make dev` (uvicorn on `0.0.0.0:8000`), `curl -s -i localhost:8000/api/v1/ready` → `HTTP/1.1 200 OK` with `{"status":"ready","checks":{"config":"ok","database":"ok"},"app_env":"development"}`; `/api/v1/health` → `{"status":"ok"}`; security headers still present. Against a broken `DATABASE_URL` (`sqlite:////nonexistent-dir/manovia.db`) the same endpoint returns `HTTP/1.1 503 Service Unavailable` with `{"error":{"code":"not_ready","message":"Database is not reachable","request_id":"c1a66a5f…"}}`.
- **6. `grep -rn "message_text\|content" backend/app/models/safety_event.py`** → no matches (exit 1). Declared columns are only `id`, `user_id`, `session_id`, `risk_level`, `source`, `created_at`.
- **Lint/format** — `make lint`: `ruff check .` all checks passed; `ruff format --check .` 60 files already formatted; `mypy app tests` (strict) "Success: no issues found in 57 source files". Root `make lint` / `make test` pass by delegation. Frontend: still README-only, nothing to lint or test.

## Decisions (ADR index)

- [0001 — Monorepo and stack](docs/adr/0001-monorepo-and-stack.md): FastAPI + Pydantic v2, SQLAlchemy 2 + Alembic + PostgreSQL, React + Vite + TypeScript + Tailwind, provider-abstracted LLM integrations with offline fakes, Docker for local orchestration.
- [0002 — Backend skeleton: configuration, logging, and error conventions](docs/adr/0002-backend-skeleton-conventions.md): error envelope, structlog JSON logging with request IDs and field redaction, env-only settings, security headers, offline ASGI-transport testing.
- [0003 — Data layer: models, migrations, encryption at rest, and repositories](docs/adr/0003-data-layer-models-migrations-and-encryption.md): typed models + naming convention, portable types (no native enums), UTC timestamps with dual defaults, erasure-as-one-`DELETE` via cascade (+ the SQLite pragma), encryption at the repository boundary with one deployment key today and per-user keys later, metadata-in-the-clear/words-encrypted split, async sessions with thin never-commit repositories, hand-reviewed migrations with drift as a test failure, readiness = connectivity (not migration state).
- Smaller calls made today, recorded here because they are not obvious from the code: `postgres://` is accepted as an alias of `postgresql://` (PaaS handout); repositories `flush` but never `commit`, and `Depends(get_session)` does not commit implicitly; `ChatRepository.end`/`UserRepository.soft_delete` write through the ORM rather than a Core `UPDATE` so a caller reading the same object back inside a request sees the new value; the seed script writes a plain `scrypt$…` demo password hash so the account is realistic before the auth milestone exists.

## Known issues

- **Session branch, not `day-03-database-models-migrations`**: the Arena session is pinned to `arena/ff090752-manovia`, so the PR comes from there.
- `greenlet` is required by SQLAlchemy's asyncio support and arrives via the `sqlalchemy[asyncio]` extra; a deploy image that installs plain `sqlalchemy` will fail at import. SQLAlchemy 2.1 **removed** `sqlalchemy.ext.mypy`, so no SQLAlchemy mypy plugin is configured (`pyproject.toml` would break mypy if one were listed).
- PostgreSQL is not exercised here: no server in the sandbox. The PostgreSQL-specific behaviours (JSONB, native `UUID`, `TIMESTAMP WITH TIME ZONE`, `BYTEA`, real FK cascade, `ON DELETE CASCADE` on a `DELETE`) are asserted by compiling the DDL for the `postgresql` dialect and by `compare_type` drift checks — not by a live server. **Check by hand** where a PostgreSQL instance exists.
- The single Fernet key in `FIELD_ENCRYPTION_KEY` protects every user's fields; there is no rotation, no per-user key, and no key-version marker in the ciphertext format, so a key change today orphans existing rows. Intentional until the key-management milestone.
- Ciphertext is base64url text (Fernet), which inflates storage ~1.4x and means the `*_encrypted` columns cannot be searched — no full-text search over messages/journals without a design change (hashed search keys, or Postgres FTS over an encrypted-at-application-layer-only field).
- Readiness checks connectivity only, so a pod whose database exists but is unmigrated reports ready; running `alembic upgrade head` is a deploy step (there is no CI or Docker orchestration yet, so it is not automated anywhere).
- The 49-day plan document is still not in the repo (`docs/plan/` holds a placeholder), so Day 4 steps below are inferred from the day-plan messages.
- This sandbox's pip is PEP 668 externally-managed; `pip config set global.break-system-packages true` was set once so `pip install -e ".[dev]"` works here. Not a repository issue.
- `pre-commit` is not installed in the sandbox, so the configured hooks (ruff, ruff-format, gitleaks) were not run.
- A local `.env` (gitignored) and `manovia.db` were created in the working tree for verification, with a throwaway `FIELD_ENCRYPTION_KEY`. Both are ignored; delete them or keep them for `curl` experiments.

## Parking lot

- Extend the log-redaction blocklist (e.g. `text`, `prompt`, `raw_input`) once chat endpoints exist, and add a redaction test for a chat request body.
- Per-user data keys: key wrapping, a `key_version` column or header so rotation is possible, and an erasure-by-key-destruction path (Day 17 in the plan).
- A purge job that turns `deleted_at` into a real deletion (plus `messages` retention when `store_chat` is withdrawn).
- Consider a partial unique index enforcing at most one *granted* row per `(user_id, kind, version)`, and `CHECK (length(region) = 2)` / `language` format constraints.
- Consider adding the field-cipher state to `/api/v1/ready` (`"field_encryption": "ok"`) once onboarding depends on it; today readiness is config + database by design.
- Password hashing belongs to the accounts milestone (argon2/bcrypt + a real hash policy); the seed script's `scrypt$…` value is demo data, not an auth design.
- LLM provider interface with offline fakes; helpline content module (`app/content/helplines.json` with `last_verified`) behind a read-only endpoint; full-text search strategy for encrypted fields; CI workflow running `make lint`, `make test`, `make migrate-check` (and a PostgreSQL service job for the migration round trip); Docker compose; frontend scaffold; evals harness.
- Restore the legacy `chatbot-1/` source under `legacy/` when supplied; add the 49-day plan to `docs/plan/` when available.

## Next steps (Day 4 — first three)

1. Accounts: `app/core/passwords.py` (argon2 or bcrypt behind a small interface, with a fake/no-op test path) + `POST /api/v1/auth/signup`, `POST /api/v1/auth/login` issuing a signed session token, and `app/api/deps.py::get_current_user` built on `UserRepository` — tests must assert that a soft-deleted or anonymous user cannot authenticate and that no password or token ever reaches the logs.
2. Onboarding and consent endpoints: `GET /api/v1/onboarding` (which agreements are outstanding) and `PUT /api/v1/consent` writing through `ConsentRepository.record` with the content version from `docs`/config, plus the `/api/v1/me` profile (language, region) — with the integration test that a withdrawal of the same version flips `is_granted`.
3. CI: add `.github/workflows/ci.yml` running `make lint`, `make test` and `make migrate-check` on Python 3.11, and a second job with a PostgreSQL service that runs the same `alembic upgrade head / downgrade base / upgrade head` round trip against real `postgresql+asyncpg` (the one thing this sandbox could not do).
