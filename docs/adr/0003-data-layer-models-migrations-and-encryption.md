# ADR 0003: Data layer — models, migrations, encryption at rest, and repositories

- **Status:** Accepted (Day 3 data layer)
- **Date:** 2026-10-09

## Context

Day 3 adds the schema every later feature sits on: users, consents, chat
sessions and messages, mood check-ins, journal entries, assessment results, and
safety events. Two constraints from `AGENTS.md` dominate the design: personal
text must not sit in the clear, and the safety trail must not become a second
copy of what a person said. The schema also has to be real on day one — SQLite
for development and tests, PostgreSQL in production, with migrations that go
forwards and backwards without hand-holding.

## Decisions

1. **SQLAlchemy 2.0 typed declarations, one table per module.** Every model uses
   `Mapped[...]` + `mapped_column` (no legacy `Column`), lives in
   `app/models/<table>.py`, and shares `app/models/base.py`. The base carries a
   `MetaData(naming_convention=...)`, so primary keys, foreign keys, indexes,
   unique and check constraints get deterministic names on both backends — the
   precondition for a downgrade that can actually drop them.

2. **Portable types only.** `sa.Uuid` for ids (native `UUID` on PostgreSQL,
   `CHAR(32)` on SQLite), `sa.LargeBinary` for ciphertext, `sa.SmallInteger` for
   the 1-5 scales and risk tiers, and
   `sa.JSON().with_variant(postgresql.JSONB(), "postgresql")` for documents.
   Enum-ish columns (`consent.kind`, `message.role`, `safety_events.source`,
   `instrument`, `band`) are `VARCHAR` + a named `CHECK` constraint built from a
   Python `StrEnum`, **not** a native PostgreSQL enum: `CREATE TYPE` cannot be
   altered transactionally, and adding a value later must be a one-line model
   change. `tests/unit/test_models.py` compiles the DDL for both dialects and
   asserts no `CREATE TYPE` appears, so this stays true.

3. **Timestamps are UTC instants with two defaults.** `created_at` is
   `DateTime(timezone=True)`, defaulted in Python (`utcnow()`, so the object is
   usable immediately) and by the server (`server_default=func.now()`, so a row
   inserted by raw SQL or a future bulk copy cannot be null). Every write path
   uses UTC; there is no local-time column anywhere.

4. **Erasure is a single `DELETE`.** Every child table has
   `ondelete="CASCADE"` on its `user_id` (and `messages` on `session_id`), and
   the parent relationships set `passive_deletes=True` so the ORM delegates the
   work to the database instead of loading and re-deleting rows in Python.
   Because SQLite ignores foreign keys unless told otherwise,
   `app/db/session.py` runs `PRAGMA foreign_keys=ON` on every SQLite connection;
   without that pragma the cascade would silently be a no-op in development.
   `User.deleted_at` remains the user-facing soft delete: it hides an account,
   while the hard delete is the "erase me" path.

5. **Free text is encrypted at the repository boundary.** `Message.content`,
   `MoodEntry.note`, `JournalEntry.title/body` are stored in `*_encrypted`
   `LargeBinary` columns via `app/core/crypto.py`: a `FieldCipher` protocol
   (`encrypt`/`decrypt`) with one `FernetCipher` implementation reading
   `FIELD_ENCRYPTION_KEY`. There is no plaintext column to write to, so a
   repository bug cannot leak by storing text in the clear; the reverse is also
   explicit — a `*_encrypted` value can only be read through a documented
   decrypting call (`ChatRepository.message_text`, `JournalRepository.body`,
   `MoodRepository.note`). Fernet is authenticated (a tampered blob raises
   `DecryptError`) and uses a fresh IV per call, so equal messages do not look
   equal in the file.
   Today one deployment key covers every field of every user; per-user data keys
   (wrapped so destroying a key destroys the data, plus rotation) are the next
   step in the key-management plan. Nothing outside `app/core/crypto.py` may
   depend on the current single-key layout, and no ciphertext format is
   backward-compatible.

6. **Metadata stays in the clear, words do not.** `risk_level`, `emotion`,
   `sentiment`, timestamps and the JSON answer totals are queryable without a
   key, because triage queries, trend charts and aggregate counts must run in
   SQL. `safety_events` therefore has **no** textual column at all: a detection
   stores ids, a tier, a source and a time. The "never copy message text into
   the safety trail" rule is enforced by a schema test that whitelists the
   allowed column types on that table, so a future `TEXT` column fails CI.

7. **Async everywhere, thin repositories.** `app/db/session.py` owns the engine
   and `async_sessionmaker`; `app/db/repos/*` are the only modules that build
   statements. A repository takes an `AsyncSession`, flushes (so ids exist), and
   **never commits** — the caller owns the transaction, and `Depends(get_session)`
   hands out a session without an implicit commit so the write boundary is
   visible in the endpoint. `Database` is built once per `create_app()` and
   disposed on application shutdown; tests inject their own.

8. **Migrations are hand-reviewed, drift is a test failure.** The initial
   migration was produced by `alembic revision --autogenerate` and then cleaned
   by hand (explicit constraint names, no batch wrappers for index creation on a
   just-created table, tables in dependency order). `alembic/env.py` resolves the
   URL through `Settings`, so a migration can never run against a database the
   app is not using, and `alembic check` plus an `compare_metadata` test fail if
   the models and the migration disagree. `render_as_batch=True` is configured for
   future SQLite ALTERs; `compare_server_default` stays off because SQLite and
   PostgreSQL render defaults differently and the noise would hide real drift.

9. **Readiness checks connectivity, not migration state.** `GET /api/v1/ready`
   returns `{"config":"ok","database":"ok"}` only when `SELECT 1` succeeds, and
   fails closed with 503 (and the standard error envelope) otherwise. It never
   echoes a DSN or a driver message. Applying migrations stays a deploy step: a
   replica must not race a schema change before it accepts traffic.

## Consequences

- A stolen or copied SQLite file contains no readable personal text. The test
  suite asserts it at the file level (`test_encryption_at_rest.py`) and the seed
  script makes it inspectable by hand.
- Erasure is one statement, and it is proven by a cascade test rather than
  assumed — including the case that must *not* cascade (a safety event with no
  user id).
- Encryption failure is loud: without `FIELD_ENCRYPTION_KEY` nothing can be
  written (and production settings refuse to start), so there is no "silently
  stored in the clear" mode.
- Two clocks of truth remain a risk if we are careless; the drift test converts
  that risk into a failing build.
- Follow-ups: per-user key wrapping and rotation; a purge job that turns
  `deleted_at` into real deletion; `CHECK` on the language/region format; and
  the per-user key lookup that replaces the single deployment key.
