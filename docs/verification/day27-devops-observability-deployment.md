# Day 27 verification — DevOps, observability and deployment (real commands, real output)

Date: 2026-10-13. Environment: Debian 12 sandbox, Python 3.11.2 venv,
Node 22, **real PostgreSQL 16.2** (bundled binaries via `pgserver`, running on
a unix socket at `/tmp/manovia-pgdata`). **Docker is not available in this
sandbox** (`which docker` → nothing), so compose-based checks were executed
as their exact command sequences against the real Postgres; the compose files
themselves are pinned by tests (`backend/tests/unit/test_prod_server.py`).

## PASS/FAIL table

| # | Check | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Production profile with Postgres: migrations + `/ready` | **PASS** (compose unavailable → same sequence run directly) | §1 |
| 2 | Missing `SECRET_KEY` in production refuses to boot | **PASS** | §2 |
| 3 | `/metrics` shows safety/guard/fallback series; no user-text labels | **PASS** | §3 |
| 4 | Sentry `before_send` scrubs a sentinel | **PASS** | §4 |
| 5 | Backup + restore drill, row counts match | **PASS** | §5 |
| 6 | Deployed URLs live | **N/A — not deployed** | §6 |
| 7 | Runbook completeness | **PASS** | §7 |

## 1. Production profile with Postgres — migrations applied, `/ready` ok

`docker compose` cannot run here, so the exact release sequence the prod
overlay encodes was executed directly against real Postgres 16.2:

```
$ psql -c "CREATE DATABASE manovia;"
$ DATABASE_URL="postgresql+asyncpg://postgres@/manovia?host=/tmp/manovia-pgdata" alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 0007 -> 0008, privacy settings and deletion receipts (Day 22)
INFO  [alembic.runtime.migration] Running upgrade 0008 -> 0009, mood check-ins: idempotency key for offline sync (Day 25)
$ DATABASE_URL=... alembic current
0009 (head)
```

Then the API in production posture (`APP_ENV=production`, `RUN_MIGRATIONS`
unset → entrypoint skips migrations, Gunicorn + `manovia_worker` Uvicorn
worker, `GUNICORN_WORKERS=1`):

```
$ gunicorn -c gunicorn.conf.py app.main:app
$ curl -s http://127.0.0.1:8000/api/v1/health
{"status":"ok"}
$ curl -s http://127.0.0.1:8000/api/v1/ready
{"status":"ready","checks":{"config":"ok","database":"ok"},"app_env":"production"}
```

The `migrate`-then-`api` ordering of `docker-compose.prod.yml` is enforced by
tests (`test_production_compose_overlay_keeps_the_release_contract`), and the
same sequence (fresh DB → `upgrade head` → `check`) runs in
`.github/workflows/release.yml` on Postgres 16.

**Graceful shutdown** (SIGTERM to the master, logged at info level):

```
[2026-10-13 03:52:38 +0000] [2315] [INFO] Handling signal: term
[2026-10-13 03:52:38 +0000] [2317] [INFO] Shutting down
[2026-10-13 03:52:38 +0000] [2315] [INFO] Shutting down: Master
graceful exit confirmed
```

**Found and fixed during this verification:** with the previous default of 2
workers, a second chat message in an ephemeral (not-saved) session 404ed —
the session store is per process. Fix: `GUNICORN_WORKERS` now defaults to 1
(commit `8fadaa7`), the test pins it, and the store-sharing work is in the
parking lot.

## 2. Missing `SECRET_KEY` refuses to boot in production

```
$ env APP_ENV=production DATABASE_URL=... FIELD_ENCRYPTION_KEY=<valid> \
    python -c "from app.main import create_app; create_app()"
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
  Value error, SECRET_KEY must be set when APP_ENV=production ...
exit code: 1
```

The same failure was observed live under Gunicorn before the key was
generated correctly:

```
[ERROR] Exception in worker process
pydantic_core._pydantic_core.ValidationError: ... FIELD_ENCRYPTION_KEY must be set when APP_ENV=production
[ERROR] Worker (pid:1880) exited with code 3.
[ERROR] Shutting down: Master
[ERROR] Reason: Worker failed to boot.
```

## 3. `/metrics` — required series, and no user text

Live production server against real Postgres. A guest account sent a benign
message and a pinned HIGH-risk fixture
(`evals/datasets/crisis_cases_dev.jsonl` wording) with a sentinel
`SENTINEL-LIVE-METRICS-77f2q` inside it.

Safety events by level (production posture, ML ensemble on — the benign
message was *also* escalated by the shipped ML artifact; see known issues):

```
manovia_safety_events_total{level="crisis",source="ml"} 1.0
manovia_safety_events_total{level="crisis",source="rules"} 1.0
```

With the legitimate `SAFETY_ML_ENABLED=false` posture, the benign message ran
the full pipeline and the remaining series moved:

```
manovia_llm_requests_total{position="0",provider="fake"} 1.0
manovia_output_guard_total{outcome="passed"} 1.0
```

**LLM outage demonstrated live** (`LLM_PROVIDER=anthropic` with no API key):
the chain degraded, the person still got HTTP 200:

```
manovia_llm_fallbacks_total{reason="not_configured"} 1.0
manovia_llm_fallbacks_total{reason="provider_failed"} 1.0
manovia_llm_fallbacks_total{reason="canned_reply"} 1.0
```

Privacy sweep of the whole exposition (all zero):

```
$ curl -s :8000/metrics | grep -c "SENTINEL-LIVE-METRICS-77f2q"   → 0
$ curl -s :8000/metrics | grep -ci "kill myself"                  → 0
$ curl -s :8000/metrics | grep -c "<session-uuid>"                → 0
```

Route templates only, e.g.:
`manovia_http_requests_total{method="POST",path="/api/v1/chat/sessions/{session_id}/messages",status="200"} 2.0`.
The Day-22 sentinel audit now also sweeps `/metrics`
(`tests/security/test_log_sentinels.py::test_the_prometheus_exposition_carries_no_sentinel`).

## 4. Sentry `before_send` scrubs the sentinel

Full-suite run (all 9 tests):
`backend/tests/security/test_sentry_scrub.py` — 9 passed. The end-to-end one
builds the real app with a DSN, forces a 500 inside the journal path while
the request body and query string carry the sentinel, captures the event at
the transport, and asserts:

- `SENTINEL-*` appears nowhere in the shipped event (body dropped, query
  dropped, **stacktrace frame locals stripped** — that is where the parsed
  body hid during development of this feature),
- `person@example.com` / `9876543210` in the exception message leave as
  typed placeholders (`[EMAIL]`, `[PHONE_NUMBER]`),
- the client-facing 500 envelope stays curated.

## 5. Backup + restore drill — row counts match

Real `pg_dump`/`pg_restore` (PostgreSQL 16.2) against the sandbox server:

```
$ ./scripts/verify_backup_drill.sh --source-url "postgresql://postgres@/manovia?host=/tmp/manovia-pgdata"
== drill: dump the source ==
backup written: /tmp/tmp.0F7Bv78rxX/manovia-20261013T034051Z.dump
backup verified (pg_restore --list ok): ...
== drill: rebuild the scratch database ==
scratch ready: manovia_drill
== drill: restore the dump into the scratch ==
restore completed from ...
--- row counts after restore ---
alembic_version=1 ... safety_events=7 ... users=25   (16 tables)
== drill: compare row counts (source vs scratch) ==
DRILL PASS: row counts match (16 tables)
```

The same drill runs nightly in CI (`.github/workflows/backup.yml`) on a
Postgres 16 service container.

## 6. Deployed URLs — what remains

Nothing is deployed from this sandbox: `flyctl`/`netlify` need platform
accounts and interactive logins the sandbox does not have. Everything up to
that line is done and verified: image (`backend/Dockerfile` runs the tested
Gunicorn command — pinned by test), `fly.toml` (release_command migrations,
`/health` checks), `netlify.toml` (SPA redirect, `/api/*` same-origin
rewrite, the nginx CSP verbatim), `docs/deploy.md` (exact steps), the
release workflow (migrations on Postgres 16 → GHCR images → changelog).

Remaining, in order (all in docs/deploy.md):
1. `fly auth login`, `fly launch --no-deploy --copy-config`, `fly postgres create/attach` (sets `DATABASE_URL`).
2. `fly secrets set` for `APP_ENV`, `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, `ALLOWED_ORIGINS` (Netlify origin), optional `SENTRY_DSN`.
3. `fly deploy` → verify `/api/v1/health` + `/api/v1/ready` on the Fly URL.
4. Edit the `/api/*` rewrite target in `netlify.toml` to the real Fly host; `netlify init && netlify deploy --prod` → verify `/` 200 and the headers.
5. Create the two scheduled machines (backup 03:00 UTC, retention 03:30 UTC, docs/backups.md).
6. Point an uptime monitor at `/api/v1/ready` and the site root.
7. Tag `v0.1.0` to exercise the release workflow end-to-end.

## 7. Runbook completeness

`docs/runbook.md` covers every scenario the brief lists, each with
symptoms → checks → actions, plus observability queries:

- LLM outage — §"LLM outage" (fallback metric triage; "never weaken the
  guard during an outage"); the outage path was exercised live (§3 above).
- DB outage — §"DB outage" (ready vs health split; restore pointer; crisis
  path stays up because triage is in-process).
- Helpline data change — §"Helpline data change" (single source file,
  schema, `last_verified`, pinned fixtures, end-to-end check).
- Key rotation — §"Key rotation" (JWT key vs field-encryption keyring, the
  Day-22 re-encrypt tooling, drill-after-rotation).
- Incident response — §"Incident response" (declare/stabilise/communicate/
  verify/follow-up, with "never weaken safety controls during an incident").
- **Safety incident** — its own section (§"Safety incident"): content-free
  evidence only, fail-closed containment, bounded exposure assessment,
  same-day human/advisor escalation, eval-gated fix-forward, sign-off.

## Final gates (wrap-up run)

```
backend:  ruff check .                        → All checks passed!
          ruff format --check .               → 337 files would be left unchanged
          mypy app tests (strict)             → Success: no issues found in 280 source files
          pytest --cov=app --cov-branch       → 3906 passed, 3 skipped (3 optional extras)
          scripts/coverage_gate.py            → PASS (overall and per-module floors hold; crypto/scoring 100%)
frontend: npm run lint                        → clean
          npm run typecheck (tsc + sw config) → clean
          npm run test:run                    → 600 passed | 3 skipped (603)
          npm run build                       → built, SW precache 25 entries
```

## Known issues found today

1. `crisis_v1.joblib` under scikit-learn 1.9.1 over-escalates aggressively in
   production posture — "What is a good way to plan my study schedule?" and
   even "hello there" landed HIGH (crisis card) with the ML ensemble on. The
   documented Day-24 precision debt (0.543) is likely worse in 1.9.1 (the
   artifact was pickled under 1.5.2; the suite already warns
   `InconsistentVersionWarning`). Rules-only (`SAFETY_ML_ENABLED=false`) is
   the safe deployment posture until retraining (Day-28 next step).
2. Ephemeral chat sessions are per process → `GUNICORN_WORKERS=1` default
   (fixed today); sharing the store is the real fix (parking lot).
3. `alembic check` on a *freshly migrated* Postgres reports three cosmetic
   `modify_type` diffs on generic `JSON` columns (`cbt_records.what_helped`,
   `deletion_receipts.rows_deleted`, `user_settings.preferred_exercise_tags`).
   Pre-existing (models predate Postgres-first); harmless but noisy — the
   release workflow's `alembic check` step would fail on it, so the step
   needs these either mapped (type_annotation) or the columns moved to
   `JSONB` deliberately. Recorded as known issue; not silently weakened.
