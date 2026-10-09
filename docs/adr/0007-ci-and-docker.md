# 0007 - CI pipeline, Docker packaging, and containerised dev stack

- Status: accepted (Day 7)
- Date: 2026-10-09
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0001 monorepo and stack](0001-monorepo-and-stack.md), [0002 backend skeleton conventions](0002-backend-skeleton-conventions.md), [0006 emotion model](0006-emotion-model.md)

## Context

Day 7 wires the integration layer: one CI workflow that mirrors the local
`make lint` / `make test` contract, container images for both halves, and a
compose stack for development. Three decisions had real alternatives.

## Decisions

### 1. One workflow, blocking checks only where we can act today

`.github/workflows/ci.yml` runs four jobs: **backend** (ruff, ruff-format,
mypy strict, `pytest --cov=app --cov-fail-under=80`), **frontend** (ESLint,
`tsc --noEmit`, Vitest, `vite build`), **secrets-scan** (gitleaks, full
history), and **dependency-audit** (pip-audit + `npm audit --omit=dev`).

- The coverage gate is **80 %**, the briefed floor. The suite actually measures
  100 % of `app/`; the gate is a tripwire against regression, not the ambition.
- The dependency audit is **report-only**: its steps always exit 0 and put
  findings in the job summary plus `::warning` annotations. The first
  implementation used `continue-on-error: true`, which keeps the workflow
  gate green but still paints the PR check red — a permanent ❌ next to every
  PR invites exactly the rubber-stamping "report only" was meant to avoid.
  The backlog it guards is known and triaged (PROGRESS.md "Known issues":
  every npm advisory needs a semver major we pinned deliberately). Flipping
  to blocking later means letting the steps exit non-zero again — a two-line
  change.
- The secrets scan **blocks**. A committed secret is never an acceptable
  trade-off.

### 2. Dev-only auto-migrations in the API container, nowhere else

The API image's entrypoint (`backend/docker-entrypoint.sh`) runs
`alembic upgrade head` only when `APP_ENV=development` (and
`RUN_MIGRATIONS=true`), then `exec`s the server. The compose stack sets
`APP_ENV=development`, so `make up` on a fresh clone lands on a migrated
database with zero manual steps.

Production is the exact opposite by construction: any other `APP_ENV` skips
migrations silently into the start log line, and production migrations remain a
deliberate, separate, observable step (`docker run … alembic upgrade head`).
The guard lives in the *entrypoint*, not in compose YAML, so the safety
travels with the image.

### 3. The API image ships without the `nlp` extra

`torch` + `transformers` would add several GB (PyPI's default torch wheel is
CUDA-enabled) to an image whose model, per ADR 0006, cannot even download
without hub access. The analyzer chain's designed degradation path — lexicon
analyzers with provenance flags — is what the container runs, with
`EMOTION_ANALYZER=keyword` in compose so the doomed HF load is not even
attempted. A model-bearing image variant (`Dockerfile` target or the
`nlp` extra with a CPU-only torch index) is in the parking lot.

### Also decided

- **Compose topology**: `postgres` is never published to the host; `web`
  (nginx) proxies `/api` to `api` so the browser only talks to its own origin —
  no CORS in the container path at all. `ALLOWED_ORIGINS` is still set for
  direct-to-API clients on `localhost:3000`.
- **Dev-only defaults in compose** (`make up` with no `.env`): a labelled
  placeholder `SECRET_KEY` and an **all-zero-bytes Fernet key** — valid format,
  zero entropy, impossible to mistake for a real secret — protecting only a
  throwaway local database. Real values come from `./.env` via interpolation.
- **Healthchecks**: containers probe `/api/v1/health` (liveness), not `/ready`
  — a hiccuping postgres must not restart-loop the API; orchestrators probe
  readiness separately. The API runs as a non-root `app` user.
- **Smoke test** (`scripts/smoke.sh`): waits for `/ready`, creates a guest,
  records consents at the fetched current versions, checks the frontend
  returns 200, and never prints tokens. `SMOKE_SKIP_COMPOSE=1` reuses the same
  checks against any externally started stack.

## Consequences

- CI is the same contract as local: a green `make lint && make test` means a
  green pipeline, so contributors are never surprised by the Actions tab.
- First real container deploy still needs: a production migration runbook, a
  decision on the model-bearing image, and the trusted-proxy/`X-Forwarded-For`
  story (a proxied deployment currently shares one rate-limit budget).
- The audit job's report-only status is revisited once the advisory backlog is
  cleared; flipping it to blocking means removing the `exit 0` guards so the
  native exit codes flow again.
