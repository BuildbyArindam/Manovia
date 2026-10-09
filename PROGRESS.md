# Manovia — Progress

## Current status

Day 4 (authentication, anonymous mode and consent) is complete and verified:
274 tests, 100% coverage, live curl/httpx checks against a running server all
pass. Work is on the session branch `arena/3a05341b-manovia`; the requested
`day-04-authentication-anonymous-mode-consent` branch was not used because this
session is fixed to the Arena session branch (same arrangement as Days 1–3).
Day 3 is merged on `main`; the Day 4 PR is open and unmerged.

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

### Day 4 (branch `arena/3a05341b-manovia`) — auth, anonymous mode, consent

**Accounts and sessions** (`app/core/passwords.py`, `app/core/tokens.py`, `app/models/refresh_token.py`, `app/db/repos/refresh_tokens.py`, `alembic/versions/0002_refresh_tokens.py`, `app/api/v1/auth.py`):

- `POST /api/v1/auth/guest` — anonymous account + real session, no email needed; `POST /api/v1/auth/upgrade` attaches email/password to the **same user id**, so guest chat/journal/mood data survives (asserted end to end).
- `POST /api/v1/auth/register` / `/login` — argon2id (`argon2-cffi`) behind a `PasswordHasher` protocol; password policy is length-only (min 10, max 256 — NIST SP 800-63B, no composition theatre); login failures are constant-time and constant-message (`invalid_credentials`), with a dummy argon2 verify for unknown accounts.
- JWT access (15 min) + refresh (7 days) with **rotation**: refresh tokens are stored as SHA-256 hashes in `refresh_tokens` with a rotation `family_id`; presenting a rotated/revoked token revokes the whole family (`401 refresh_token_reused`); `POST /auth/logout` revokes the presented family (idempotent, no oracle); upgrade revokes all families. `get_current_user` accepts access tokens only and rejects soft-deleted accounts (anonymous users authenticate — that is guest mode).
- `GET /api/v1/auth/me` — the simple protected endpoint.

**Consent** (`app/content/consent_documents.json`, `app/content/documents.py`, `app/api/v1/consent.py`, `app/api/deps.py`):

- `GET /api/v1/consent/requirements` (public) — current versions of `terms` / `privacy` / `ai_disclosure` / `store_chat` plus the AI disclosure text (AGENTS.md rule 3).
- `POST /api/v1/consent` — append-only grants at the current document version (`422 consent_version_mismatch` otherwise); withdrawal is a new `granted=False` row and the newest row wins.
- `require_consent(*kinds)` dependency checks grants **at the current version** and returns `403 consent_required` naming the missing kinds. Wired on `POST /api/v1/chat/sessions` — the first (deliberately tiny) consent-gated surface; message send arrives with the chat milestone.

**Abuse controls** (`app/core/ratelimit.py`, `app/core/lockout.py`, `app/core/middleware.py`):

- In-house sliding-window rate limiting (no slowapi): strict on `/api/v1/auth/*` (default 10/min per client IP), moderate globally (120/min), `Retry-After` on every 429, configurable and disable-able (`RATE_LIMIT_*`).
- Account lockout with exponential backoff keyed by attempted email: 5 failures → 15-min lock, doubling to a 1-hour cap, cleared by a successful sign-in (`LOGIN_*` settings).
- `ApiError` + handler carries curated codes (`invalid_credentials`, `token_expired`, `refresh_token_reused`, `consent_required`, `account_locked`, `rate_limited`, `email_taken`, `already_registered`, …) through the Day 2 error envelope; credentials join the log-redaction blocklist.

**Tests**: 274 tests, 100% coverage of `app/` (1416 statements) — 273 Day 4 additions/updates across `tests/unit/{test_passwords,test_tokens,test_ratelimit,test_lockout,test_consent_documents,test_api_deps,test_config,test_errors}.py` and `tests/integration/{test_auth_api,test_refresh_rotation,test_consent_api,test_rate_limit_api,test_log_redaction_e2e}.py`, plus updates where the schema grew (`test_models`, `test_migrations`, `test_seed_script`). The seed script now hashes its demo password with the real argon2 hasher (the `scrypt$` placeholder from Day 3 is gone) and stamps consents with the shipped document versions.

## Verification (Day 4 — real command output)

- **Deps** — `pip install -e ".[dev]" argon2-cffi pyjwt`: argon2-cffi 25.1.0, PyJWT 2.15.1 alongside fastapi 0.143.0 / sqlalchemy 2.1.4 / pytest 9.1.1.
- **Migration** — `alembic upgrade head` on a fresh DB: `Running upgrade -> 0001, initial schema` then `Running upgrade 0001 -> 0002, add refresh_tokens`; `alembic check` → `No new upgrade operations detected.`
- **`make lint`** — `ruff check .` all checks passed; `ruff format --check .` 82 files already formatted; `mypy app tests` (strict) "Success: no issues found in 78 source files".
- **`make test`** — **274 passed** in ~23 s, coverage TOTAL **1416 stmts / 0 miss / 100 %**. `app/api/v1/auth.py` 148 stmts 100 %; `app/core/{passwords,tokens,ratelimit,lockout}.py`, `app/content/documents.py`, `app/db/repos/refresh_tokens.py`, `app/api/deps.py` all 100 %.
- **Live requests against `uvicorn app.main:app` on :8000** (real TCP httpx, paced across the 10/min auth windows — see "Known issues" for why pacing was needed): **16/16 checks passed**.
  1. Guest: `POST /auth/guest` → 201 + token pair; `GET /auth/me` with the token → 200; without → 401 `token_missing`.
  2. Register → login → refresh (rotates) → logout; the logged-out refresh token → `401 refresh_token_reused`.
  3. Reuse of an already-rotated token → `401 refresh_token_reused`, and the legitimate successor token is dead too (whole family revoked).
  4. `POST /chat/sessions` before consenting → 403 `consent_required` ("Consent required: ai_disclosure, terms."); `GET /consent/requirements` → 4 versions + AI disclosure text; after `POST /consent` → 201.
  5. 15 rapid bad logins → attempts 1–5 `401 invalid_credentials`, 6–9 `429 account_locked` (Retry-After), 10–15 `429 rate_limited`; the correct password is also refused while throttled.
  6. **Log grep** over the captured server log (62 lines): 0 occurrences of the two passwords, 0 JWT-shaped strings (`eyJhbGciOiJIUzI1NiIs`), 0 of the three issued token signatures, 0 credential-named fields. Only method/path/status/request_id lines.
- **Two failures during verification were fixed at the root, with regression tests**:
  - The test suite's coverage showed auth code as ~60 % "missing" while every request succeeded: SQLAlchemy's async sessions resume coroutine frames from **greenlets** and coverage silently dropped line events (and under dotted `--cov` sources the compiled cache-key module crashed with `TypeError: 'InternalTraversal' object is not callable` — upstream [coveragepy#2324](https://github.com/coveragepy/coveragepy/issues/2324) / [sqlalchemy#13645](https://github.com/sqlalchemy/sqlalchemy/issues/13645)). Fixed with `[tool.coverage.run] concurrency = ["greenlet", "thread"]` — numbers are honest again (and the crash is gone).
  - The first live verification run tripped the auth rate limit mid-script (correct product behaviour, wrong script pacing): re-run paced across windows, all green. A follow-up flake (2 lines of `create_app`'s ephemeral-key branch only covered when no developer `.env` exists) got a deterministic regression test (`test_missing_secret_key_falls_back_to_ephemeral_token_signing`).
- The Day 4 test suite itself caught one real bug during development: `POST /auth/refresh` minted a **new** family instead of rotating inside the presented one, which would have neutered reuse detection (`test_reusing_a_rotated_token_revokes_the_whole_family` + `test_revoked_timestamps_are_set_on_rotation` are the regression guards).

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
- [0004 — Authentication, anonymous mode, and consent](docs/adr/0004-authentication-and-consent.md): argon2id + length-only password policy, fixed-HS256 access/refresh JWTs with hashed storage and rotation-family reuse detection, consent documents as versioned content enforced at the *current* version by `require_consent`, in-house sliding-window rate limiting + per-account lockout with backoff, `ApiError` curated codes in the Day 2 envelope.
- Smaller calls made on Day 4, recorded here because they are not obvious from the code: login and upgrade return the same `invalid_credentials`/`email_taken` shapes whether or not the account exists (login is constant-time; registration cannot hide that an address is taken); logout is possession-based and idempotent so it never becomes an account oracle; `upgrade` revokes every refresh family because an identity change should sign everything out; a consent version bump closes gated features until re-consent (intended); `alembic/versions/0002` was autogenerated and hand-reviewed in the 0001 style (named constraints, explicit downgrade); models gained `as_utc()` because SQLite hands back naive datetimes and `expires_at` comparisons must not mix naive/aware.

## Known issues

- **Session branch, not `day-04-authentication-anonymous-mode-consent`**: the Arena session is pinned to `arena/3a05341b-manovia`, so the PR comes from there (same as Days 1–3).
- **Rate limiting and lockout are per-process memory**: behind several uvicorn workers or pods each gets its own budget and a restart clears locks. A distributed store (Redis) must go behind the same `RateLimiter`/`LoginLockout` interfaces before production; until then an attacker with many source IPs gets more attempts, and one shared egress IP (NAT/proxy) shares one budget. `X-Forwarded-For` is deliberately **not** trusted yet — a trusted-proxy setting is needed before keying on it, or every client behind a reverse proxy shares the proxy's budget.
- **Access tokens are not revocable before expiry** (15 min): logout/upgrade revoke refresh tokens only; a stolen access token lives until `exp`. Short TTL is the whole mitigation; a revocation list or introspection endpoint is a later decision.
- **Registration/upgrade reveal that an email is taken** (`409 email_taken`) — login does not. Deliberate for usable sign-up; revisit with a "check your email" flow if enumeration becomes a real threat.
- **Account lockout is a targeted annoyance**: 5 failed attempts lock a victim's account for 15–60 minutes with no email notification or self-service unlock (needs the email-verification milestone).
- **HS256 shared secret, no `kid`**: the signing key is the verification key and rotating `SECRET_KEY` invalidates every session at once. Fine for one service; asymmetric keys + key ids when a second verifier exists.
- **No breached-password screening** (e.g. k-anonymity HIBP lookup) — length-only policy means "aaaaaaaaaaaa" is still allowed.
- `greenlet` is required by SQLAlchemy's asyncio support and arrives via the `sqlalchemy[asyncio]` extra; a deploy image that installs plain `sqlalchemy` will fail at import. SQLAlchemy 2.1 **removed** `sqlalchemy.ext.mypy`, so no SQLAlchemy mypy plugin is configured (`pyproject.toml` would break mypy if one were listed).
- **Coverage needs `concurrency = ["greenlet", "thread"]`** (set in `pyproject.toml`): without it, coverage silently under-reports after SQLAlchemy awaits and can crash runs with dotted `--cov` sources via the compiled cache-key module ([coveragepy#2324](https://github.com/coveragepy/coveragepy/issues/2324)). Do not remove that config; and never take coverage numbers from a run without it.
- PostgreSQL is not exercised here: no server in the sandbox. The PostgreSQL-specific behaviours (JSONB, native `UUID`, `TIMESTAMP WITH TIME ZONE`, `BYTEA`, real FK cascade, `ON DELETE CASCADE` on a `DELETE`) are asserted by compiling the DDL for the `postgresql` dialect and by `compare_type` drift checks — not by a live server. **Check by hand** where a PostgreSQL instance exists.
- The single Fernet key in `FIELD_ENCRYPTION_KEY` protects every user's fields; there is no rotation, no per-user key, and no key-version marker in the ciphertext format, so a key change today orphans existing rows. Intentional until the key-management milestone.
- Ciphertext is base64url text (Fernet), which inflates storage ~1.4x and means the `*_encrypted` columns cannot be searched — no full-text search over messages/journals without a design change (hashed search keys, or Postgres FTS over an encrypted-at-application-layer-only field).
- Readiness checks connectivity only, so a pod whose database exists but is unmigrated reports ready; running `alembic upgrade head` is a deploy step (there is no CI or Docker orchestration yet, so it is not automated anywhere).
- A soft-deleted account keeps its unique `email` slot, so re-registering that address returns `email_taken` until the purge job runs.
- The 49-day plan document is still not in the repo (`docs/plan/` holds a placeholder), so day scopes are inferred from the day-plan messages.
- This sandbox's pip is PEP 668 externally-managed; `pip config set global.break-system-packages true` was set once so `pip install -e ".[dev]"` works here. Not a repository issue. Scripts from the install (pytest, mypy, ruff, uvicorn) live in `~/.local/bin`, which is not on this sandbox's default `PATH`.
- `pre-commit` is not installed in the sandbox, so the configured hooks (ruff, ruff-format, gitleaks) were not run.
- A local `.env` (gitignored) and `manovia.db` were created in the working tree for the live verification, with throwaway `SECRET_KEY`/`FIELD_ENCRYPTION_KEY` values. Both are ignored; delete them or keep them for `curl` experiments. Note the `.env` also feeds the module-level `create_app()` at import time (by Day 2 design).

## Parking lot

- Distributed rate limiting / lockout state (Redis) behind the existing interfaces, plus a trusted-proxy setting for `X-Forwarded-For` client identity.
- Breached-password screening (k-anonymity HIBP) and an email-verification / password-reset flow; unlock notification emails.
- Refresh-token binding to a client (DPoP or at least a user-agent fingerprint) and a `kid`-based key rotation story; access-token revocation list or short-TTL + introspection trade-off review.
- Extend the log-redaction blocklist as new features land; a redaction test per request body shape.
- Per-user data keys: key wrapping, a `key_version` column or header so rotation is possible, and an erasure-by-key-destruction path (Day 17 in the plan).
- A purge job that turns `deleted_at` into a real deletion (plus `messages` retention when `store_chat` is withdrawn, and freeing soft-deleted emails).
- Consider a partial unique index enforcing at most one *granted* row per `(user_id, kind, version)`, and `CHECK (length(region) = 2)` / `language` format constraints.
- Consider adding the field-cipher state to `/api/v1/ready` (`"field_encryption": "ok"`) once onboarding depends on it; today readiness is config + database by design.
- LLM provider interface with offline fakes; helpline content module (`app/content/helplines.json` with `last_verified`) behind a read-only endpoint; full-text search strategy for encrypted fields; CI workflow running `make lint`, `make test`, `make migrate-check` (and a PostgreSQL service job for the migration round trip); Docker compose; frontend scaffold; evals harness.
- Restore the legacy `chatbot-1/` source under `legacy/` when supplied; add the 49-day plan to `docs/plan/` when available.

## Next steps (Day 5 — first three)

1. **Chat core with the safety gates first**: `app/content/helplines.json` (with `last_verified`) + the deterministic crisis rules that run **before** any LLM call (AGENTS.md rule 1), as a pure module with unit tests over fixture messages — no network.
2. **LLM provider interface**: `app/llm/` with a `ChatCompleter` protocol, one real provider stub and a `FakeCompleter` for offline tests, config-selected via `LLM_PROVIDER` (already in `.env.example`); plus the output-safety check every completion must pass (AGENTS.md rule 4).
3. **Message send on top of Day 4**: `POST /api/v1/chat/sessions/{id}/messages` behind `require_consent(ai_disclosure, terms)` and `get_current_user`, storing text through `ChatRepository.add_message` (encrypted), emitting `SafetyEvent` rows, and asserting in tests that no raw message text reaches the logs at any level (AGENTS.md rule 5).
