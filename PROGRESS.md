# Manovia — Progress

## Current status

Day 7 (integration, CI and dockerisation) is complete and verified as far as
this sandbox allows: **623 backend tests pass at 99.96 % coverage** (2381
statements; the one missed line is the lazy-load retry guard in `hf.py`, only
reachable with the `nlp` extra installed — the full-environment figure stays
100 %) and **73 frontend tests** still pass, with `ruff`, `ruff format`,
`mypy` (strict), `eslint`, `tsc --noEmit` and the production `vite build` all
clean. The repo now has a four-job GitHub Actions pipeline, Docker images for
both halves, a compose dev stack with dev-only auto-migrations, an end-to-end
smoke script, and a real CI badge in the README.

What could **not** be verified here: **Docker does not exist in this sandbox**
(`docker`/`docker compose`/`gitleaks`/`actionlint` are absent and their release
binaries cannot be downloaded — network egress is restricted), so image builds,
the compose stack, container health/`whoami` checks and `docker compose logs`
are statically reviewed only. Every check that *can* run here ran for real —
including the smoke test's full request sequence against a live uvicorn +
production `vite preview` (`SMOKE PASS`, exit 0, plus three verified failure
modes). The short list of commands to run on a Docker machine is in
"Verification (Day 7)" below.

Work is on `arena/729849e3-manovia` — the branch this Arena session is pinned
to, **not** the requested `day-07-integration-ci-dockerisation`; the session
cannot create or push to another branch name. The pull request will therefore
come from the session branch with `day-07-integration-ci-dockerisation` as
its base, matching how previous days landed. Days 1-6 are merged on `main`
(PRs #1-#7).

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

### Day 5 (branch `day-05-frontend-skeleton-react-vite`) — frontend skeleton

**Toolchain** (`frontend/package.json`, `vite.config.ts`, `tsconfig.json`, `eslint.config.js`, `tailwind.config.js`, `postcss.config.js`, `.prettierrc.json`): Vite 7 + React 18.3.1 + TypeScript 5.9 (strict, plus `noUncheckedIndexedAccess`) + Tailwind 3.4 + React Router 6.30 + TanStack Query 5, tested with Vitest 5 + Testing Library + jsdom, linted with ESLint 9 flat config (typescript-eslint, react-hooks) and Prettier 3.9. Node 22 / npm 10 in this sandbox. Pinned deliberately *below* the current majors — React 19, react-router 7, Tailwind 4 and ESLint 10 are not used (the task pins React 18 and a stable chain).

**Design tokens** (`frontend/src/index.css`): every colour, font and focus colour in the app is a `--manovia-*` custom property, with two complete palettes — warm paper (light) and warm charcoal (dark) — swapped by `data-theme` on `<html>` (no `dark:` classes, no duplicated colour decisions). `index.html` carries a six-line inline script that sets `data-theme` before first paint. `--manovia-font-sans` is the system sans stack and `--manovia-font-serif` an Iowan/Palatino/Georgia stack — no external font requests, nothing to block the first paint.

**Accessibility floor** (each item asserted in the suite, not left to review): skip link as the first focusable element; `header` / `nav` / `main` / `footer` landmarks; contrast ≥ 4.5:1 for every text pair in **both** themes (light min 5.42:1, dark min 6.55:1) and ≥ 3:1 for the focus ring and strong borders, checked arithmetically by `src/styles/tokens.test.ts` because `jest-axe` disables colour-contrast rules in jsdom; a real focus trap in every dialog with focus restored to the opener; `prefers-reduced-motion` honoured in CSS **and** in JS (`usePrefersReducedMotion`); `html { font-size: 16px }`; onboarding step changes move focus to the new heading and are announced in a polite live region.

**Layout** (`src/components/AppShell.tsx`, `Header.tsx`, `SideNav.tsx`, `BottomNav.tsx`, `src/navigation.ts`): one navigation object drives both presentations — a side rail ≥768px, a fixed bottom bar below. `AppShell` asks `useMediaQuery("(min-width: 768px)")` and renders one of them, so the markup matches what the user can actually reach and the behaviour is testable with a stubbed `matchMedia`.

**Crisis dialog** (`CrisisHelpButton.tsx`, `CrisisResourcesModal.tsx`): a persistent "Need help now?" button in the header on **every** route (mobile and desktop), opening a modal over the scroll-locked page that lists helplines from the backend. It is keyboard reachable from every route, and the emergency instruction ("if you are in danger, call your local emergency number now") is static copy, so a failed fetch is never a dead end. The backend half is `backend/app/api/v1/crisis.py` → `GET /api/v1/crisis/resources`, **public** (no auth, no consent gate — someone in trouble has not signed in), serving `backend/app/content/helplines.json` (8 resources, `last_verified`, `source`, `disclaimer`, `resources[]`), validated on load by `app/content/crisis.py` (unique ids, ≥1 contact method per resource, dialable characters only, `priority` 0–100).

**Onboarding** (`src/onboarding/`): welcome → "What Manovia is and is not" → AI disclosure → agreements → guest or account. `App` renders the flow *instead of* the shell until the agreements are recorded, so no surface is reachable without them; the consent gate is enforced twice (disabled button **and** a re-check inside `OnboardingProvider.complete()`), each agreement is a separate checkbox and every one starts unticked, and the optional one (chat history) is never required. Document versions and the AI disclosure text come from `GET /api/v1/consent/requirements`, with an offline fallback that `src/onboarding/content.test.ts` keeps in step with `backend/app/content/consent_documents.json` (a drift guard).

**API client** (`src/lib/api.ts`, `src/lib/endpoints.ts`): the only code that talks to the backend. Failures become an `ApiError` carrying the Day 2 envelope `{error: {code, message, request_id}}`; a 401 triggers exactly one single-flight `POST /auth/refresh` (concurrent 401s share it) and exactly one replay; proactive refresh when the access token is inside a 30 s skew; a failed refresh clears the session and calls `onAuthExpired`. Token store and clock are injected, which is what makes the refresh tests possible without a browser. Endpoint shapes live in `src/lib/endpoints.ts`, so a backend rename is a compile error.

**Tests** — 12 files / 73 tests, all passing: `lib/api.test.ts` (15: 401 → refresh → replay, no double replay, proactive refresh, single-flight, `ApiError` envelope parsing), `styles/tokens.test.ts` (9: both palettes' ratios, focus/border contrast, 16px base, reduced-motion block, the accent stays sage), `App.test.tsx` (8: the shell's landmarks, skip link, the help button on all six routes, each route's h1, the 404, both navigations), `onboarding/OnboardingFlow.test.tsx` (8: cannot complete without the required consents, each checkbox starts unticked, the optional one is not required, step content, storage persistence), `components/Modal.test.tsx` (7: focus trap, Escape, backdrop, focus restoration, scroll lock, aria wiring), `lib/mediaQueries.test.tsx` (5), `onboarding/storage.test.ts` (5), `lib/contrast.test.ts` (4), `CrisisResourcesModal.test.tsx` (4), `CrisisHelpButton.test.tsx` (3), `lib/focus.test.ts` (3), `onboarding/content.test.ts` (2).

**Backend additions** (15 new tests, 274 → 289): `app/content/helplines.json`, `app/content/crisis.py`, `app/api/v1/crisis.py`, `HelplinesDep` in `app/api/deps.py`, the router in `app/main.py`; `tests/unit/test_crisis_content.py` (9) and `tests/integration/test_crisis_api.py` (6) cover the invariants, the 404-style empty case, public access, and the response shape.

**Also changed**: `.gitignore` gained `*.tsbuildinfo`; the root `Makefile` gained `frontend-dev` / `frontend-test` / `frontend-lint` / `frontend-format` and now runs both halves for `make test` / `make lint` / `make format`; `frontend/README.md` replaced its placeholder; `backend/README.md` documents the crisis endpoint; `README.md` documents both quick starts.

### Day 6 (branch `arena/284986a6-manovia`) - NLP service (emotion and sentiment)

**The contract** (`app/services/nlp/base.py`): `EmotionAnalyzer.analyze(text, lang)
-> EmotionResult{primary, scores, valence, arousal}` plus provenance (`analyzer`,
`model`, `language`, `truncated`, `confidence`, `cached`). The internal taxonomy
is nine labels - `joy, sadness, anger, fear, anxiety, shame, loneliness, calm,
neutral` - each with one hand-set `(valence, arousal)` anchor in
`EMOTION_DIMENSIONS`, so valence/arousal are *derived*, never invented per
analyzer. `scores` is always a normalised distribution even when the model
underneath is multi-label; the largest raw probability is kept separately as
`confidence`. Results are validated hard (unknown label, out-of-range valence,
`primary` disagreeing with `scores` all raise), and `is_informative` distinguishes
"a confident neutral" from "I found nothing".

**`HFEmotionAnalyzer`** (`hf.py`): a `text-classification` pipeline over
`EMOTION_MODEL_ID`, multi-label (`top_k=None`), `device=-1` (CPU),
`truncation=True` + `EMOTION_MAX_LENGTH`, batched at `EMOTION_BATCH_SIZE`, and
pre-cut by character count so a 10,000-character paste cannot blow up
tokenisation. Loading is lazy and thread-safe (double-checked lock) and a failure
is **remembered**, so a bad model id costs one warning rather than one failed
import per request. `LABEL_MAP` folds a checkpoint's labels into the taxonomy -
GoEmotions (27), Ekman (7) and `positive`/`negative` are all covered - taking the
**max** per emotion rather than the sum (summing `sadness` + `grief` would invent
confidence). Unmapped labels are dropped and warned about once.
`transformers`/`torch` are imported *inside* the loader and ship as an optional
`nlp` extra, so the suite and CI never need them.

**The fallbacks**: `KeywordFallbackAnalyzer` (a nine-label lexicon - English,
romanised Hindi, Banglish, and Devanagari/Bengali script - with negation,
intensifiers and emphasis; `confidence` capped at 0.6 because a keyword hit is not
a probability) and `SentimentAnalyzer` (the legacy chatbot's polarity idea: two
weighted lists, a running score, `tanh`-squashed into `[-1, 1]`, with negation and
"but"-contrast). Shared pragmatics live once in `lexicon.py`. Keyword runs first
because it can name all nine emotions; sentiment runs second because it can only
band polarity but catches words the emotion lexicon misses.

**Language** (`language.py`): script detection (Devanagari/Bengali) beats
everything, then weighted **Hinglish** heuristics (104 romanised-Hindi markers, so
"exam kal hai, bahut dar lag raha hai" reads as `hi` with `hinglish=true`), then
`langdetect` folded to `en`/`hi`/`bn`/`other` with a pinned seed for determinism.
Plain-ASCII prose that langdetect misreads as Italian is assumed English with
confidence capped at 0.5 - an assumption, never presented as a detection.

**Cache** (`cache.py`): a thread-safe LRU keyed by
`sha256(variant | lang | whitespace-collapsed text)`. The plaintext is stored
nowhere; `describe()` returns counters only. The model id is part of the key, so
swapping checkpoints cannot serve stale answers; case is preserved because
shouting is a signal.

**Degradation** (`chain.py`): `AnalyzerChain` puts the model first and the lexicons
behind it, falling through on failure **or** on a zero-confidence neutral, and
never raises - if everything fails the caller gets a neutral result with
`analyzer="unavailable"` (deliberately not cached, so a transient outage is not
sticky). `ModelHealth` is a circuit breaker (`EMOTION_FAILURE_THRESHOLD` failures
opens it for `EMOTION_COOLDOWN_SECONDS`) plus a latency governor (EMA above
`EMOTION_SLOW_MS` stops routing to the model). Both were corrected during
verification: the sample that pays for the model *load* is excluded
(`EmotionAnalyzer.is_warm`), and the slow rule needs at least 3 samples, so one
slow call cannot switch the model off.

**Endpoint** (`app/api/v1/dev.py`): `POST /api/v1/dev/analyze` runs one string
through the chain in a worker thread and returns the result plus which analyzer
answered; `GET /api/v1/dev/analyze/state` exposes chain health and cache counters.
Mounted **only** when `APP_ENV != production` (so it is 404 *and* absent from the
OpenAPI schema there), with an in-endpoint re-check as defence in depth. The log
line carries a 16-hex fingerprint and a length, never the text; `text`, `prompt`
and `query` were added to the log blocklist.

**Config** (`EMOTION_ANALYZER`, `EMOTION_MODEL_ID`, `EMOTION_DEVICE`,
`EMOTION_MAX_LENGTH`, `EMOTION_BATCH_SIZE`, `EMOTION_CACHE_SIZE`,
`EMOTION_FAILURE_THRESHOLD`, `EMOTION_COOLDOWN_SECONDS`, `EMOTION_SLOW_MS`), all
validated at startup - a typo in `EMOTION_ANALYZER` fails loudly instead of
silently meaning "no analyzer". Documented in `.env.example`, `backend/README.md`
and [ADR 0006](docs/adr/0006-emotion-model.md).

**Tests** (630 backend, 100 % coverage of `app/`; 341 new):
`tests/unit/test_nlp_{base,lexicon,keyword,sentiment,language,cache,fake,hf,chain,factory}.py`,
emotion settings in `tests/unit/test_config.py`,
`tests/integration/test_dev_analyze.py` (29),
`tests/integration/test_nlp_hf_pipeline.py` (the real `transformers` pipeline
against a tiny locally-built model, no download), and
`tests/integration/test_nlp_model.py` (the one `@pytest.mark.model` test,
deselected by default via `addopts = "-ra -m 'not model'"`).

### Day 7 (branch `arena/729849e3-manovia`) — integration, CI and dockerisation

**CI** (`.github/workflows/ci.yml`): one workflow, four jobs, mirroring the
local `make lint` / `make test` contract exactly. `backend` — ruff check, ruff
format --check, mypy strict, `pytest --cov=app --cov-fail-under=80` (the
briefed floor; the suite measures well above it). `frontend` — ESLint,
`tsc --noEmit`, Vitest, production `vite build`. `secrets-scan` — gitleaks over
full history, **blocking**. `dependency-audit` — pip-audit +
`npm audit --omit=dev`, **report-only** (`continue-on-error: true`) until the
triaged advisory backlog is worked down (ADR 0007). Dependencies are cached via
`setup-python`/`setup-node` keyed on `pyproject.toml`/`package-lock.json`;
runs trigger on pushes to `main` and every PR, with per-ref concurrency
cancellation and `contents: read` permissions only. The companion
`.github/workflows/README.md` documents it.

**Images** (`backend/Dockerfile`, `frontend/Dockerfile`, `docker/nginx.conf`,
`.dockerignore`): the API image is multi-stage (wheels into a venv in a slim
builder; a BuildKit pip cache keeps rebuilds fast), runs as non-root `app`,
healthchecks `/api/v1/health` (liveness — a postgres blip must not
restart-loop the API) via python's urllib (the slim image has no curl), and
serves uvicorn with `--no-access-log` (AGENTS.md rule 5, same as `make dev`).
The web image builds with `npm ci` + `npm run build` (which typechecks) and
serves the bundle via nginx with hashed-asset caching, SPA fallback, security
headers, and a same-origin `/api` proxy to the api service — no CORS, no
localhost calls from the browser. `.dockerignore` keeps `.env*`, `.git`,
caches and `node_modules` out of both build contexts. The `nlp` extra is
deliberately absent from the API image (ADR 0007 §3): compose sets
`EMOTION_ANALYZER=keyword`, so the lexicon analyzers answer instead of paying a
doomed multi-GB model load.

**Compose stack** (`docker-compose.yml`): `api` + `web` + `postgres:16-alpine`
with `.env` interpolation and labelled dev-only defaults — a placeholder
`SECRET_KEY` and an all-zero-bytes Fernet key (valid format, zero entropy,
impossible to mistake for a real secret), so `make up` works with no `.env` at
all. Postgres is never host-published and `api` waits on its `pg_isready`
healthcheck. `make up` / `make down` / `make smoke` are real targets now; only
`make eval` remains a placeholder.

**Dev-only auto-migration** (`backend/docker-entrypoint.sh`, task 3): the entrypoint
runs `alembic upgrade head` only when `APP_ENV=development` **and**
`RUN_MIGRATIONS=true`, then `exec`s the server. The guard lives in the image,
not the compose file, so production posture (migrations as a deliberate,
separate, observable step) travels with the image itself.

**Smoke test** (`scripts/smoke.sh`, task 4): starts the stack (skippable via
`SMOKE_SKIP_COMPOSE=1`), waits for `/api/v1/ready` with a timeout, creates a
guest (`POST /auth/guest` → 201), records consents at the fetched current
document versions (`POST /consent` → 201), and checks the frontend returns
HTTP 200. Never prints tokens or payloads; a dead service surfaces as a
curated `SMOKE FAIL` line with exit 1 (both verified for real, see
Verification). URL/timeout/compose-command overrides are env vars.

**README** (task 5): the CI badge now points at the real workflow; the coverage
badge carries the measured backend figure; the quick start leads with
`cp .env.example .env` (optional) → `make up` → `make smoke` → `make down`,
keeps the no-Docker path, and documents that the dev container auto-migrates.

**Week-1 code review** (task 6): reviewed the security-sensitive core
(`crypto`, `tokens`, `passwords`, `middleware`, `ratelimit`, `lockout`,
`logging`, `errors`, `config`, `db/session`, `services/nlp/hf`, `alembic/env`,
plus a TODO/FIXME sweep across backend+frontend — none found) against the AGENTS.md
rules. The **top five technical debts** are recorded at the head of "Known
issues" below. The sub-15-minute ones were fixed today: the README's stale
Day 1 claim that log redaction is "not implemented", the workflows README
placeholder, the stale quick-start/roadmap (Docker targets listed as
placeholders), the missing `POSTGRES_*` section in `.env.example`, and
smoke.sh's bare `curl` exit codes on connection failure.

## Verification (Day 7 — real command output)

Everything below was run for real in this sandbox (Python 3.11.2, Node 22.22.3,
npm 10.9.8; backend deps in `backend/.venv`; **no Docker, gitleaks or
actionlint available**). Nothing is claimed from documentation.

`make lint` (repo root, venv on PATH):

    ruff check .            -> All checks passed!
    ruff format --check .   -> 111 files already formatted
    mypy app tests          -> Success: no issues found in 107 source files
    npm run lint (eslint .) -> clean (no output)

`make test` (repo root):

    pytest --cov=app --cov-report=term-missing
      -> 623 passed, 2 skipped in 26.50s
         (skips: the two nlp-extra integration tests — transformers is not
          installed here; the 5 `-m model` tests are deselected by addopts)
         TOTAL 2381 statements, 1 missed = 99.96%
      pytest --cov=app --cov-report=term-missing --cov-fail-under=80
      -> Required test coverage of 80% reached. Total coverage: 99.96%
    npm run test:run -> Test Files 12 passed (12), Tests 73 passed (73)
    npm run typecheck (tsc --noEmit) -> clean
    npm run build -> vite v7.3.7, 104 modules, dist/assets/index-*.js
      236.79 kB (75.28 kB gzip), built in 2.36s

Dependency audits (the CI job's exact commands, run locally):

    pip-audit -> "No known vulnerabilities found"
      (first run flagged only the sandbox venv's *build tool* setuptools
       66.1.1 — not a runtime dependency; clean after
       `pip install --upgrade setuptools`. Alpine/python:3.12-slim CI images
       ship a current setuptools, so this is a sandbox artefact.)
    npm ci --omit=dev && npm audit --omit=dev -> 2 moderate, both in
      react-router (GHSA-wrjc-x8rr-h8h6 open redirect, GHSA-337j-9hxr-rhxg SSR
      hydration) — the same two advisories triaged on Day 5: neither is
      reachable in this SPA, and the fix is the deliberately-avoided semver
      major react-router 7. This is exactly why the audit job is report-only.

Smoke script — run for real against a live bare-metal stack (uvicorn on :8000
with a migrated sqlite DB + `vite preview` on :4173 serving the production
build; `SMOKE_SKIP_COMPOSE=1` skips only the compose start):

    ==> Waiting for API readiness at http://127.0.0.1:8000/api/v1/ready ...
        API ready (HTTP 200)
    ==> Creating a guest account
    ==> Recording consents at the current document versions
    ==> Checking the frontend at http://127.0.0.1:4173/
    SMOKE PASS: API ready, guest created, consent recorded, frontend HTTP 200
    EXIT=0

    Failure modes verified: API down -> "SMOKE FAIL: API never reported
    ready" exit 1; web down -> "SMOKE FAIL: frontend returned HTTP 000"
    exit 1. Tokens never printed.
    bash -n and sh -n: clean for smoke.sh and docker-entrypoint.sh.

API logs during the smoke runs (the sandbox stand-in for
`docker compose logs api | tail -30`): structlog JSON lines only —
startup warnings naming *missing keys by name* (`field_encryption_key_missing`,
`secret_key_missing_ephemeral_token_signing` — no values), `event: http_request`
lines carrying method + **path only** + status + request_id. No query strings,
no tokens, no user text, no stack traces, no SQL.

Workflow YAML validation: `actionlint` could not be downloaded (egress
restriction, the release host is blocked), so the brief's fallback ran —
PyYAML parse + structural assertions (valid mapping; triggers `push:[main]` +
`pull_request`; concurrency group; `contents: read`; every job has `runs-on`
and every step `uses` or `run`). Jobs: `backend` (7 steps),
`frontend` (7 steps), `secrets-scan` (2 steps), `dependency-audit` (5 steps,
continue-on-error = report only). The first true lint of the workflow is its
first Actions run.

### Docker — run these on a machine with Docker (impossible in this sandbox)

    docker compose build                       # both images
    make up                                    # or: docker compose up -d --build
    make smoke                                 # or: ./scripts/smoke.sh
    docker compose ps                          # api/web/postgres healthy
    docker compose exec api whoami             # must print: app (never root)
    docker compose logs api | tail -30         # JSON lines, no traces/secrets/text
    docker compose down -v                     # stop and wipe the dev volume

### PASS/FAIL table (Message 2 checklist)

| # | Check                                              | Result |
|---|----------------------------------------------------|--------|
| 1 | `make lint && make test` clean                     | **PASS** |
| 2 | compose build/up + `scripts/smoke.sh`              | **BLOCKED (no Docker)** — smoke logic + failure modes PASS against the bare-metal equivalent; see commands above |
| 3 | `compose ps` healthy; `exec api whoami` != root    | **BLOCKED (no Docker)** — Dockerfile evidence: `USER app`, non-root build; healthcheck present on both images |
| 4 | `compose logs api` — no traces/secrets/user text   | **PASS (equivalent)** — live uvicorn logs during smoke inspected, clean (see above) |
| 5 | `docker compose down -v`                           | **BLOCKED (no Docker)** |
| 6 | Workflow YAML valid; jobs listed                   | **PASS (fallback)** — PyYAML structural validation, 4 jobs listed; actionlint unavailable |
| 7 | Top-5 Week-1 debts recorded                        | **PASS** — at the head of "Known issues"; sub-15-min ones fixed today |

## Verification (Day 6 — real command output)

Everything below was run for real in this sandbox (Python 3.11.2, Node 22.22.3,
npm 10.9.8, `torch` 2.14.1 + `transformers` 5.19.0 installed for the pipeline
checks). `huggingface.co` is **not** reachable from it, so no real checkpoint was
ever downloaded.

- **Install** - `pip install --user -e ".[dev]"` (fastapi 0.143.0, pydantic 2.14.0,
  sqlalchemy 2.1.4, pytest 9.1.1), then `langdetect` 1.0.9, then
  `torch` + `transformers` for the pipeline test. `cd frontend && npm install` ->
  334 packages.
- **`make test` with `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`** - exit 0.
  Backend: **627 passed, 5 deselected in 38.6 s, coverage TOTAL 2368 stmts / 0 miss
  / 100 %**. Frontend: **12 files / 73 tests passed** in 12.0 s. (Re-run after the
  latency fix below: **630 passed, 5 deselected, TOTAL 2381 / 0 / 100 %**.)
- **`make lint`** - `ruff check .` all checks passed; `ruff format --check .` 111
  files already formatted; `mypy app tests` (strict) "Success: no issues found in
  107 source files"; `npm --prefix frontend run lint` (`eslint .`) clean.
- **`pytest -m model -q`** - **5 failed, 627 deselected in 28.5 s**. Root cause
  from the traceback: `huggingface_hub.errors.LocalEntryNotFoundError: An error
  happened while trying to locate the file on the Hub and we cannot find the
  requested files in the local cache.` The analyzer logged exactly what it should:
  `{"model_id": "SamLowe/roberta-base-go_emotions", "error_type": "OSError",
  "fallback": "lexicon", "event": "emotion_model_unavailable"}`. **These 5 must be
  re-run where the hub is reachable** (`pip install -e ".[dev,nlp]"`).
- **The fallback path, verified live instead** - `tests/integration/test_nlp_hf_pipeline.py`
  builds a 1-layer DistilBERT with a hand-written vocabulary in a temp dir and runs
  the genuine `transformers.pipeline(...)` through `HFEmotionAnalyzer`
  (`_build_pipeline`, `device=-1`, `truncation`, `top_k=None`, batching, empty-text
  short-circuit, concurrent single load, bad path -> `ModelUnavailableError`):
  **6 passed, offline**.
- **Live server, the six requested cases** (`uvicorn app.main:app`, `curl`,
  `EMOTION_ANALYZER=keyword`):

  | text | primary | valence | arousal | analyzer | lang |
  | --- | --- | --- | --- | --- | --- |
  | "I got the job and I can't stop smiling" | `joy` | +0.800 | 0.60 | keyword | en |
  | "I feel so alone lately" | `loneliness` | -0.600 | 0.30 | keyword | en |
  | "exam kal hai, bahut dar lag raha hai" | `fear` | -0.600 | 0.70 | keyword | hi / hinglish |
  | "I am fine" | `calm` | +0.400 | 0.20 | keyword | en |
  | "" (empty) | `neutral` | 0.000 | 0.30 | chain | other |
  | 10,000 x "x" | `neutral` | 0.000 | 0.30 | keyword | other |

  No crashes. All six returned HTTP 200 with a full nine-label `scores` map.
- **Latency, 20 sequential calls** (unique text each call, so no cache hits):

  | path | p50 | p95 | min | max | mean |
  | --- | --- | --- | --- | --- | --- |
  | model (`hf`, tiny local 2-layer model) | **4.8 ms** | **7.1 ms** | 4.4 | 7.5 | 5.0 |
  | fallback (`keyword` lexicon) | **2.6 ms** | **3.6 ms** | 2.4 | 3.9 | 2.8 |
  | cache hit (same text x20) | 3.4 ms | 3.5 ms | - | - | - |

  Cold start dominates everything else: the first request against a cold model took
  **4479 ms** (lazy load + inference). **These are not the production model's
  numbers** - the local model is a 2-layer/64-dim stub with random weights, built
  because the real checkpoint cannot be downloaded. Re-measure p95 against
  `SamLowe/roberta-base-go_emotions` (125M params) before trusting
  `EMOTION_SLOW_MS=1500`.
- **`APP_ENV=production`** - `POST /api/v1/dev/analyze` -> **HTTP 404**
  `{"error":{"code":"not_found",...}}`; `GET /api/v1/dev/analyze/state` -> **404**;
  the route is **absent from `/openapi.json`** (13 paths, none containing `dev`);
  `GET /api/v1/health` still 200. Asserted in tests too
  (`test_the_route_does_not_exist_in_production`,
  `test_the_route_is_absent_from_the_production_schema`).
- **Log grep over both live servers** (200 lines) - occurrences of "I got the job",
  "stop smiling", "I feel so alone", "alone lately", "exam kal hai", "bahut dar
  lag", "I am fine", "xxxxxxxxxx", "anxious about thing": **all 0**. The only
  `text`-prefixed fields anywhere are `text_sha` (92) and `text_length` (92); the
  events emitted are `http_request` (96), `emotion_analyzed` (92),
  `emotion_analyzer_configured` (2), `emotion_model_loaded` (1). Also asserted
  in-test with a sentinel (`test_the_text_is_never_logged`).
- **Two failures were found during verification and fixed at the root**, each with a
  regression test (no test was weakened or deleted):
  1. **The tokenizer split Indic words at combining marks.** `\w` matches
     Devanagari/Bengali *letters* but not their vowel signs (Unicode Mn), so
     "अकेला" tokenised as "अक" + "ल" and **no** Hindi or Bengali lexicon term could
     ever match. Fixed by including the mark ranges in the word pattern;
     `test_devanagari_words_stay_whole`, `test_bengali_words_stay_whole` and the
     `test_indic_scripts` parametrization are the guards.
  2. **The cold model load poisoned the latency governor.** The first call's 4479 ms
     seeded the EMA above `EMOTION_SLOW_MS`, so every later request went to the
     keyword fallback for the life of the process (chain state showed
     `ema_ms=3604.9, skipped_slow=24, degraded=24` against a healthy model). Fixed by
     excluding the load sample (`EmotionAnalyzer.is_warm`) and requiring 3 samples
     before the slow rule engages. Re-measured: `samples=24, ema_ms=1.9,
     skipped_slow=0, degraded=0`. Guards:
     `test_a_cold_start_is_not_measured_as_inference`,
     `test_one_slow_call_does_not_switch_the_model_off`.
- Three further bugs were caught by the new tests during development, not by the
  live run: `polarity()` matched signed keys the tokenizer never produces (the whole
  sentiment lexicon was dead, and injected lexicons were ignored entirely); `scan()`
  reported "up" as a separate hit after matching "fed up"; and one lexicon entry was
  mixed-script (Devanagari SHA + Bengali matras) so it matched nothing. Two dead
  3-word lexicon entries were removed because the scanner only builds bigrams -
  `test_phrases_are_at_most_two_words` now enforces that.


## Verification (Day 5 — real command output)

Everything below was run for real in this sandbox (Node 22.22.3, npm 10.9.8, Python 3.11).

- **Install** — `cd frontend && npm install` → 333 packages, no vulnerabilities reported; `cd backend && pip install --user -e ".[dev]"` succeeded.
- **`cd frontend && npm ci`** — **334 packages**, no lockfile drift, then `npm run lint`, `npm run typecheck`, `npm run test -- --run` and `npm run build` all green from the clean install. `npm audit` reports 9 advisories (4 moderate, 5 high, 0 critical) and every available fix needs a **semver major** (Tailwind 4 for the build-time `braces`/`chokidar`/`micromatch`/`fast-glob`/`postcss-*` tree — none of which ships in the bundle — or react-router 7 for two moderate advisories). Neither react-router advisory is reachable here: the SSR `deserializeErrors` hydration issue needs a server rendering mode this SPA does not use, and the open-redirect-via-backslash issue needs a user-controlled `to`/`navigate()` target, while every route is a hard-coded literal from `src/navigation.ts`.
- **`npx tsc --noEmit`** — clean (strict, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`).
- **`npx eslint .`** — clean. Six errors were found and fixed at the root: four `importOriginal()` type annotations inside `vi.mock` factories (replaced by `vi.mock(..., { spy: true })`), a `useEffect` placed above an early `return` in `OnboardingFlow.tsx`, and an unused type parameter in the jest-axe typings (split into `src/test/jest-axe.d.ts` + `src/test/vitest-matchers.d.ts`).
- **`npx prettier --write .`** then **`npx prettier --check .`** — clean (`printWidth` 100, double quotes, trailing commas, `arrowParens: "always"`).
- **`npx vitest run`** — **12 files / 73 tests passed** in ~12.3 s: `api` 15, `tokens` 9, `App` 8, `OnboardingFlow` 8, `Modal` 7, `onboarding/storage` 5, `mediaQueries` 5, `contrast` 4, `CrisisResourcesModal` 4, `CrisisHelpButton` 3, `focus` 3, `onboarding/content` 2.
- **`npm run build`** (`tsc --noEmit && vite build`) — succeeded: `dist/assets/index-*.css` **14.82 kB (gzip 3.96 kB)**, `dist/assets/index-*.js` **236.79 kB (gzip 75.28 kB)**.
- **Backend** — `ruff check .` all checks passed; `ruff format --check .` 86 files already formatted; `mypy app tests` (strict) "Success: no issues found in 82 source files"; `python3 -m pytest --cov=app -q` → **289 passed** (274 + 15 new) with **TOTAL 100 %** coverage (`app/content/crisis.py` 53 stmts 100 %, `app/api/v1/crisis.py` 8 stmts 100 %).
- **Live stack** — `uvicorn app.main:app` on :8000 (with `alembic upgrade head` applied to a throwaway `manovia.db`) and `vite` on :5173 (`server.host: true`, `/api` proxied to `VITE_API_PROXY_TARGET ?? http://127.0.0.1:8000`), both verified with `curl`:
  - **"Need help now?" on ≥ 3 routes** — **no browser binary exists in this sandbox** (Playwright browsers cannot be downloaded: no CDN access), so the check was done two ways. (a) `curl` on `/`, `/chat`, `/mood`, `/journal`, `/exercises`, `/insights`, `/settings` and an unknown route → 200 with `<title>Manovia</title>` and the root div on every one, and `/api/v1/crisis/resources` → 200 through the proxy and directly (8 resources). (b) `src/App.test.tsx` renders the shell in jsdom and asserts the help button exists on all six routes, then opens the dialog and checks the helplines render. A real browser pass over onboarding + the help modal is listed under "Known issues" for hand-checking.
  - **Onboarding + crisis dialog in a rendered tree** — `jest-axe` runs over the onboarding flow (all 5 steps) and over the shell and the open help dialog: **0 serious or critical violations** in each case (colour-contrast rules are disabled by `jest-axe` in jsdom, which is why contrast is asserted arithmetically on the tokens instead).
  - **Consent checkboxes unticked by default** — `OnboardingFlow.test.tsx` asserts every agreement checkbox (including the optional `store_chat`) renders unchecked, that the agreements step cannot be advanced from with no boxes ticked, and that ticking only the optional one still leaves the primary action disabled.
  - **`prefers-reduced-motion`** — `src/styles/tokens.test.ts` asserts the CSS block exists and disables animation/transition durations, `usePrefersReducedMotion` is unit-tested with a stub that can change the query's `matches`, and the dev server's generated CSS was fetched and confirmed to contain the `@media (prefers-reduced-motion: reduce)` block and `html { font-size: 16px }`.
  - **Live wire flow against the running API** — `POST /api/v1/auth/guest` → token pair; `GET /consent/requirements` → 4 documents, only `ai_disclosure` carries text; `POST /api/v1/consent` with the 3 required grants → 201; `GET /auth/me` → the anonymous user; a bad refresh token → 401 `refresh_token_reused`.
- **Live preview** — the app was served and is running as the "Manovia web app" process (`http://localhost:5173` and the sandbox's proxied preview URL), with the "Manovia API" process on :8000. **What must be checked by hand in a real browser**: the dark palette's appearance, the onboarding flow's step transitions, the help modal's focus trap and the theme switcher.

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
- [0005 — Frontend skeleton: tokens, the API client, and the accessibility floor](docs/adr/0005-frontend-skeleton.md): CSS-variable design tokens with a `data-theme` switch (no `dark:` classes, no literal colours in components), contrast enforced by a test on the tokens, one API client with single-flight token refresh and one replay, onboarding as a gate rather than a guarded route, one modal primitive owning the focus contract, one navigation rendered as rail or bottom bar by a media query, and a deliberately public crisis endpoint.
- [0006 — Emotion model: choice, mapping, and licence](docs/adr/0006-emotion-model.md): `EMOTION_MODEL_ID` as the single place a checkpoint is named (default `SamLowe/roberta-base-go_emotions`, MIT), one nine-label internal taxonomy with a `LABEL_MAP` that takes the **max** per emotion rather than the sum, lazy thread-safe CPU loading with `transformers`/`torch` as an optional extra, degradation on both failure *and* sustained slowness, and the licence position (model MIT verified from three independent mirrors; the GoEmotions **dataset** licence still to be confirmed by hand). Numbered 0006 because the brief's requested `0002-emotion-model.md` was already taken on `main` by the backend-skeleton ADR.
- [0007 — CI pipeline, Docker packaging, and containerised dev stack](docs/adr/0007-ci-and-docker.md): one workflow whose blocking checks mirror `make lint`/`make test` (80 % coverage gate as tripwire, secrets scan blocks, dependency audit report-only until the triaged advisory backlog clears); dev-only auto-migrations guarded in the API image's *entrypoint* (`APP_ENV=development` + `RUN_MIGRATIONS=true`), so production posture travels with the image; the API image ships without the `nlp` extra and compose runs `EMOTION_ANALYZER=keyword`; same-origin `/api` proxy in the web container (no CORS in the container path); labelled dev-only compose defaults including an all-zero-bytes Fernet key; liveness (not readiness) as the container healthcheck; the API runs as non-root `app`.
- Smaller calls made on Day 6, recorded here because they are not obvious from the code: the fallback order is keyword-then-sentiment (the keyword analyzer can name all nine emotions; sentiment only bands polarity but catches words the emotion lexicon misses); a zero-confidence neutral falls through while a *confident* neutral stops the chain; `scores` is normalised over the taxonomy even for a multi-label model, with the raw max kept as `confidence`; `truncated` on the model path is a conservative proxy (`len(text) > max_length`) because the true answer needs tokenising; keyword `confidence` is capped at 0.6 so a word match never looks like a probability; the cache key preserves case because shouting is a signal; failed-everything results are not cached so a transient outage cannot become sticky; and the fingerprint length constant was renamed from `KEY_BYTES` to `FINGERPRINT_HEX_LENGTH` because it was a hex length, not bytes.
- Smaller calls made on Day 4, recorded here because they are not obvious from the code: login and upgrade return the same `invalid_credentials`/`email_taken` shapes whether or not the account exists (login is constant-time; registration cannot hide that an address is taken); logout is possession-based and idempotent so it never becomes an account oracle; `upgrade` revokes every refresh family because an identity change should sign everything out; a consent version bump closes gated features until re-consent (intended); `alembic/versions/0002` was autogenerated and hand-reviewed in the 0001 style (named constraints, explicit downgrade); models gained `as_utc()` because SQLite hands back naive datetimes and `expires_at` comparisons must not mix naive/aware.

## Known issues

### Top 5 technical debts — Week 1 code review (Day 7)

Ranked by blast radius; each was confirmed by reading the code, not inferred
from notes. Full background on most of these is in the per-day entries below.

1. **Emotion accuracy is unmeasured (no eval harness).** `evals/` holds empty
   `datasets/`/`reports/` directories only; every label so far was
   plausibility-checked by hand against six messages, and the lexicons are
   single-author with no held-out data. Everything downstream (mood tracking,
   the upcoming crisis rules) consumes this output. The eval harness is in the
   parking lot; until it lands, no emotion label is validated.
2. **Per-process state silently scales wrong.** Rate limiting, login lockout,
   the analysis cache and the NLP circuit breaker are all in-memory
   (`InMemoryRateLimiter`, `LoginLockout`, `AnalysisCache`, per-chain breaker
   state): a second uvicorn worker or pod doubles real budgets and forks model
   health state, and `X-Forwarded-For` is deliberately untrusted — so clients
   behind a proxy (including Day 7's nginx web container) share one budget.
   Needs a shared store behind the existing interfaces plus a trusted-proxy
   setting; interfaces were designed for it.
3. **PostgreSQL has never actually run in any verification.** Every test uses
   SQLite; postgres behaviour is asserted only by compiling DDL for the
   dialect. `create_db_engine` sets no `pool_pre_ping`/pool sizing, and
   `/ready` checks connectivity, not migration level. Day 7's compose stack is
   the first place postgres really runs — and this sandbox has no Docker to
   prove it. A postgres service job in CI + the local commands above close
   this.
4. **Model supply chain and ops.** `EMOTION_MODEL_ID` pins no revision hash
   (`hf.py` `_build_pipeline` takes the moving default branch), PyPI's torch
   wheel is CUDA-enabled (~5.7 GB in Day 6's environment), there is no startup
   pre-warm so the first request pays the load, and the 5 `-m model` tests
   need network. The API image sidesteps size/network today by shipping
   without the `nlp` extra (ADR 0007 §3); the pin/CPU-wheel/pre-warm decisions
   are still owed before the model serves real users.
5. **Refresh tokens are XSS-readable in `localStorage`.** Deliberate Day 5
   trade (ADR 0005, noted in `src/lib/api.ts`): the API speaks
   `Authorization: Bearer`, so tokens live in web storage. The correct shape
   before a broad launch is an httpOnly refresh cookie + CSRF handling; it is
   a backend change, parked until the auth surface next opens.

Fixed from the review the same day (all < 15 min): README's stale Day 1 claim
that redaction is "not implemented in this scaffold" (it has been real since
Day 2, live-verified Day 6); the workflows-README placeholder; the stale
quick-start table and roadmap; the missing `POSTGRES_*` compose settings in
`.env.example`; smoke.sh exiting with bare `curl` codes instead of curated
`SMOKE FAIL` messages on connection failure.

### Day 7 operational notes

- **Day 7's branch is not the requested one**, same as most earlier days: the
  Arena session is pinned to `arena/729849e3-manovia`; the PR opens from there
  with `day-07-integration-ci-dockerisation` as its base.
- **Docker, gitleaks and actionlint do not exist in this sandbox** and their
  release binaries cannot be fetched (egress restriction). Image builds, the
  compose lifecycle, container health/`whoami`/logs checks and the CI
  secrets-scan are therefore unverified here; the exact local commands are
  listed in "Verification (Day 7)". Everything else ran for real.
- **The nginx master process runs as root** in the standard `nginx:1.27-alpine`
  image (workers run as `nginx`). The API container — the one holding secrets
  and data access — is the mandated non-root one (`USER app`); an unprivileged
  nginx variant is a later hardening option.
- **Clients of the web container share one rate-limit budget** because the API
  sees the proxy's address and `X-Forwarded-For` is untrusted (debt #2).
  Fine for the dev topology; wrong for a shared deployment.
- **The CI workflow's first real validation is its first run.** YAML parsing
  and structural checks passed here, but actionlint semantics (expression
  typing, action input schemas) only run on GitHub. Watch the Actions tab on
  the first push and expect to tweak the audit job's pip resolution if the
  runner's image differs from this sandbox.
- **The coverage badge reads 100 %** — the full-environment figure (Day 6,
  `nlp` extra installed). Sandbox/CI runs without the extra measure 99.96 %
  (one lazy-load guard line in `hf.py` uncovered). The enforced gate is 80 %;
  both figures are far above it.
- The frontend production build emits an informational chunk-size note for the
  ~237 kB JS bundle (75 kB gzip); code-splitting by route is a later
  optimisation, not a correctness issue today.


- **Day 6's branch is not the requested one.** The brief asked for `day-06-nlp-service-emotion-sentiment`; this Arena session is pinned to `arena/284986a6-manovia` and cannot create or push to another branch name. Same situation as Days 1-3 and Day 4.
- **The real emotion model was never downloaded or run here.** `huggingface.co` is unreachable from this sandbox, so `pytest -m model -q` fails all 5 with `LocalEntryNotFoundError`. **Run locally**: `pip install -e ".[dev,nlp]" && pytest -m model -q`. The pipeline *integration* is covered offline by `test_nlp_hf_pipeline.py` (a tiny locally-built checkpoint through the real `transformers` code path), so what is unverified is the specific checkpoint, not the plumbing.
- **The Day 6 latency numbers are not the production model's.** The "model path" p50 4.8 ms / p95 7.1 ms came from a 2-layer, 64-dim stub with random weights, because nothing else was loadable. `SamLowe/roberta-base-go_emotions` is 125M params; expect a materially higher p95 on CPU. Re-measure before trusting `EMOTION_SLOW_MS=1500`, and expect a multi-second cold start on first request (no pre-warm at startup today).
- **Accuracy is unmeasured - this is the biggest Day 6 gap.** No eval set or harness exists yet (`evals/` holds `datasets/` and `reports/` directories only). The six verification cases were checked for *plausibility*, not correctness, and the lexicons are hand-written by one person with no held-out data. Do not treat any emotion label as validated.
- **The default model is English-only.** Hindi and Bengali text is detected and routed, but the model reads it as noise; those messages are effectively served by the lexicon fallbacks (which do carry romanised Hindi, Banglish and Indic-script terms). A multilingual checkpoint is the real fix and is in the parking lot.
- **`loneliness` has no head in GoEmotions**, so it can only come from the lexicons - "nobody has called in weeks" is caught by a word list or not at all. `shame` and `anxiety` map thinly from `embarrassment`/`remorse` and `nervousness`.
- **`langdetect` is confidently wrong on short text** - it reads "I am fine" as Italian with probability 1.0. Plain-ASCII prose that it fails on is therefore assumed English with confidence capped at 0.5. That is a deliberate trade for a product that speaks en/hi/bn, but it means short French/German messages are labelled `en`.
- **The legacy `chatbot-1/` source is still not in the repository**, so `SentimentAnalyzer` ports the *idea* (two weighted lists, a running score, a squashed `[-1,1]` result) rather than the code. Re-diff against the real source when it arrives.
- **The analysis cache and the circuit breaker are per-process.** Behind several workers each has its own cache and its own view of model health, and a restart clears both. Correct but wasteful; a shared store would have to be keyed the same way (never by the text).
- **`torch` is installed from PyPI, which ships the CUDA-enabled wheel (~5.7 GB here).** A CPU-only deployment should use the extra index from pytorch.org; nothing in the code needs CUDA (`device=-1`), but the install size is a real deploy cost.
- **`transformers` 5.x is what was tested, not the `>=4.44` floor declared in the `nlp` extra.** `_normalize_batch` accepts all three output shapes the pipeline can produce, but 4.x was not exercised.
- **The dev endpoint is unauthenticated** outside production, by design (it is a debugging surface). Anything that reaches a non-production deployment over a network can analyze arbitrary text through it, and it is rate limited only by the global budget.
- **Day 5's branch is the requested one, Days 1–4's were not**: Day 5 work is on `day-05-frontend-skeleton-react-vite`, the name that was asked for. Days 1–3 used `arena/*` session branches (the Arena session was pinned to those names) and Day 4's PR therefore comes from `arena/3a05341b-manovia`.
- **No browser-level test anywhere in this sandbox**: there is no browser binary and none can be downloaded (no CDN access), so Playwright / `@axe-core/playwright` cannot run. The frontend is verified with `jest-axe` on the jsdom-rendered tree plus `curl` against the live dev server. **Check by hand in a real browser before trusting Day 5's UI**: the dark palette, the onboarding step transitions, the help modal's focus trap and Escape handling, the theme switcher, the responsive navigation at 768px, and a real screen-reader pass over the help dialog.
- `jest-axe` disables colour-contrast rules in jsdom (it cannot compute styles there), so contrast is asserted arithmetically by `src/styles/tokens.test.ts` on the token values — not by an axe audit. The test is the enforcement point.
- **`npm audit` reports 9 advisories (4 moderate, 5 high, 0 critical)** and every fix requires a semver major: Tailwind 4 (for `braces`, `chokidar`, `micromatch`, `fast-glob`, `postcss-nested`, `postcss-selector-parser` — build-time only, nothing shipped) or react-router 7 (two moderate advisories). React Router 6 was pinned deliberately; neither advisory is reachable in this app (the SSR hydration issue needs a server render mode this SPA does not use; the open-redirect issue needs a user-controlled route target and every route is a hard-coded literal). Re-review when Tailwind 4 / react-router 7 are adopted for real.
- **The refresh token lives in `localStorage`** (XSS-readable). An httpOnly refresh cookie is the safer design but needs a backend change with CSRF consequences (the API currently speaks `Authorization: Bearer`). Recorded as a trade-off with a note in `src/lib/api.ts` and in [ADR 0005](docs/adr/0005-frontend-skeleton.md), not fixed.
- **Frontend coverage is not measured**: no coverage threshold or reporting is configured for Vitest (the 100% bar is the backend's). The backend suite keeps its 100 % gate; the frontend's equivalent is the axe + contrast + interaction assertions listed in the Day 5 section.
- **`tailwind.config.js` still declares `darkMode: ["selector", '[data-theme="dark"]']`** purely so `dark:` variants *work* if ever used; the design system itself never uses them. Removing it is harmless but would break any future `dark:` usage silently.
- Tailwind opacity modifiers (`bg-surface/95`) do not work on colours defined as plain `var(--manovia-*)`, so no custom token colour uses one. Only opacity-free utilities are applied to tokens.
- `jsdom` cannot execute `<script type="module">`, so a jsdom test cannot smoke-test the built or dev bundle; the served bundle is checked with `curl` and the component tree with Vitest.
- **Helplines are placeholder content** (8 resources, `source` field says so, `last_verified` 2026-10-09) pending human verification against real registries — a Day 8 task. The endpoint is public by design; a wrong or stale helpline is the risk, so the `last_verified` date is validated on load.
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
- httpOnly refresh cookie (frontend + backend) to replace the `localStorage` refresh token, with CSRF handling.
- Frontend test coverage reporting and thresholds (Vitest `--coverage`), and a real browser pass in CI (Playwright or `@axe-core/playwright`) over onboarding, the help dialog and both themes — blocked today by the sandbox, not by the repo.
- Dark-mode palette and motion preferences as a service-worker-cached setting; "reduce motion" is honoured, but a per-user motion toggle is not offered.
- LLM provider interface with offline fakes; full-text search strategy for encrypted fields; CI workflow running `make lint`, `make test`, `make migrate-check` (and a PostgreSQL service job for the migration round trip); Docker compose; evals harness; a PWA/offline story for the crisis dialog (a cached copy of the helplines so it works with no network).
- Frontend error-boundary and offline states: today a failed request surfaces as a toast-level message; a global boundary with a retry affordance and a "you are offline" variant are not built.
- Restore the legacy `chatbot-1/` source under `legacy/` when supplied; add the 49-day plan to `docs/plan/` when available.
- **An emotion eval harness**: a labelled set (English, Hinglish, Bengali) under `evals/datasets/`, a runner that reports per-label precision/recall for each analyzer, and a threshold the CI can fail on. Without it every claim about accuracy is a guess.
- **A multilingual emotion checkpoint** for Hindi and Bengali, or a language-routed pair of models; plus a code-mixed (Hinglish) eval slice, which no public model handles well.
- **Model pre-warm at startup** (behind a setting) so the first user message does not pay the multi-second load, and a `warm` flag in `/api/v1/ready`.
- **Per-user or per-session emotion context**: today every message is analyzed in isolation, so "still" / "again" / "worse than yesterday" carry nothing. A short-window aggregate is what mood tracking actually needs.
- Batch analysis endpoint / `analyze_many` exposure for backfill jobs, and a shared (Redis) analysis cache behind the existing `AnalysisCache` interface.
- Streaming or async model loading so a slow first load cannot hold a worker thread, plus a hard inference deadline (today the latency governor reacts *after* slow calls, it cannot interrupt one).
- Extend the log-redaction blocklist test to every new request-body shape, and a CI job that greps a captured log for a planted sentinel.
- **(Day 7)** A model-bearing API image variant (separate Dockerfile target or the `nlp` extra with the CPU-only torch index), with the revision pin / pre-warm decisions from debt #4.
- **(Day 7)** A postgres service job in CI running the migration round trip and repo tests against the real engine (closes the remainder of debt #3), plus a compose-based smoke job once the stack is proven on a maintainer machine.
- **(Day 7)** Flip `dependency-audit` to blocking (`continue-on-error: false`) once the triaged advisory backlog is cleared; review the two react-router advisories when react-router 7 is adopted.
- **(Day 7)** Unprivileged nginx image or a `nginxinc/nginx-unprivileged` base for the web container; route-based code-splitting when the bundle justifies it.
- **(Day 7)** Production migration runbook (one-shot `alembic upgrade head` container) and a `/ready` migration-level check once deploys exist.

## Next steps (Day 8 — first three)

**Preamble (carried from Day 7, needs my machine):** run the seven Docker
commands listed in "Verification (Day 7)", confirm `make up` + `make smoke`
are green, `whoami` prints `app`, and the api logs stay clean — then the
compose stack is the default dev environment for Day 8+.

1. **Crisis and self-harm rules that run BEFORE any LLM call** (AGENTS.md rule 1, the
   headline): a pure, deterministic module over `app/content/` phrase sets with
   risk tiers 0-3, no model/DB/LLM needed, unit-tested against fixture messages.
   Same milestone: **human-verify `helplines.json`** against real registries and
   refresh `last_verified` (flagged as a Day 8 task on Day 5 — the content is
   still placeholder).
2. **LLM provider interface**: `app/llm/` with a `ChatCompleter` protocol, one real
   provider adapter, and a `FakeCompleter` for offline tests, selected by
   `LLM_PROVIDER` — plus the output-safety check every completion must pass
   (AGENTS.md rule 4), built the same way as `app/services/nlp/`.
3. **Message send**: `POST /api/v1/chat/sessions/{id}/messages` behind
   `require_consent(ai_disclosure, terms)` and `get_current_user`, storing text
   encrypted via `ChatRepository`, running the crisis rules **first**, attaching
   the Day 6 `EmotionResult`, emitting `SafetyEvent` rows, with tests asserting
   no raw message text reaches the logs. Then wire the frontend chat page to it.
