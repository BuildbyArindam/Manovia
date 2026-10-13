# 0026 - Deployment platform: Fly.io for the API and Postgres, Netlify for the web

- Status: accepted (Day 27)
- Date: 2026-10-13
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0007 CI and Docker](0007-ci-and-docker.md), [0022 privacy controls](0022-privacy-controls-and-data-export.md), [0024 evaluation harness](0024-evaluation-harness.md), [docs/deploy.md](../deploy.md)

## Context

Day 27 needs a real place to run Manovia: a FastAPI container, a PostgreSQL
database, a scheduled home for the nightly backup and retention jobs, and a
static host for the Vite bundle. Constraints that actually narrow the choice:

- The API is already a **Docker image** with a reviewed production posture
  (non-root, no access log, Gunicorn+Uvicorn workers). The platform must run
  that image as-is, not rebuild it with a buildpack.
- Migrations must run as a **deliberate release step** (`alembic upgrade head`
  before the new containers take traffic), never on boot in production.
- Two **scheduled jobs** (nightly backup, nightly retention) need a place to
  run with the platform's secret store, not a developer laptop.
- The audience is India-first (helplines are Indian; `en`/`hi`/`bn`), so a
  region near India matters for latency on the SSE chat stream.
- Secrets (DSN, `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, `SENTRY_DSN`) must go in
  a platform secret store; the repo holds none.

## Decisions

### 1. API + Postgres: Fly.io

- Runs the repository's own `backend/Dockerfile` (`fly deploy` pushes the
  built image), so there is exactly one packaging path: the image CI tests is
  the image production runs.
- `[deploy] release_command = "alembic upgrade head"` is Fly's native
  "migrate before deploy" — the same one-shot migration container the local
  production compose overlay runs (`docker-compose.prod.yml`). Prod never
  auto-migrates on boot (`RUN_MIGRATIONS=false`, checked by tests).
- Fly's managed Postgres sits on the private network; the nightly backup is a
  **scheduled machine** running `scripts/backup_db.sh` against it, and the
  retention job is another scheduled machine (`docs/backups.md`). Scheduled
  machines are the feature Render and the rest did not offer as cleanly.
- `primary_region = "bom"` (Mumbai) puts the API next to most users; Fly's
  proxy terminates TLS and forwards to the app over their private network.
- Health checks hit `/api/v1/health` (liveness, no DB dependency, so a
  database blip does not flap the machine); uptime monitoring uses
  `/api/v1/ready` from outside (see the runbook).

### 2. Web: Netlify

The frontend is a static Vite bundle. Netlify and Vercel are functionally
equivalent here; Netlify won on one concrete point: `netlify.toml` carries the
**same strict security headers as the nginx container** (CSP copied verbatim
from `docker/security-headers.conf`), so the static host and the container
path enforce one policy instead of two. The `/api/*` **200 rewrite** to the
Fly origin keeps the browser same-origin — no CORS surface, no CSP
`connect-src` relaxation, and the strict CSP ships unchanged. Deploy previews
give every PR a URL, which is also how a reviewer checks `ALLOWED_ORIGINS`
before it is widened.

### 3. What we gave up

- **Render** was the serious runner-up: managed Postgres and pre-deploy
  commands are fine, but scheduled jobs are cron-in-a-container DIY, and its
  image-backed deploys are second-class next to buildpacks.
- **Self-hosted VPS** was rejected on ops burden: one maintainer with no
  on-call rotation should not own disk health at 3 a.m.
- **Vercel** for the web was a coin flip; the header-parity argument above
  settled it.

## Consequences

- Deploys are `fly deploy` (API) and `git push`/Netlify build (web); the
  tagged-release workflow (`.github/workflows/release.yml`) automates the
  image publish + changelog half.
- Secrets live in `fly secrets` / Netlify env; `.env.example` documents every
  name. Rotating one is a runbook procedure.
- The nightly backup machine is a real cost line (~ Shared-CPU-1x/1h per
  night); acceptable, and cheaper than the data.
- Fly-specific knobs (machine suspend policy, concurrency) are documented in
  `fly.toml` comments rather than spread across docs.

## Verification

- `docs/deploy.md` walks the full first deploy; every command in it was
  either executed in the sandbox against a real Postgres 16, or is marked as
  needing platform credentials (see `docs/verification/day27-*`).
- The migration-as-release-step contract is enforced by
  `backend/tests/unit/test_prod_server.py` (compose overlay, entrypoint) and
  exercised by the release workflow's migration job.
