# Manovia — Progress

## Current status

Day 11 (the chat orchestrator) is built and verified: **1700 backend tests pass,
3 skip** (the optional `nlp`/`llm` extras are not installed here), `ruff`,
`ruff format` and `mypy` (strict) are clean, and the **89 frontend tests** pass
unchanged. Day 11 adds **167 tests**: `tests/chat/` (159: orchestrator, ephemeral
store, prompting, repository, and the HTTP/SSE API) plus 8 settings tests.
Nothing in them touches the network or needs a key; the model is the Fake.

One message now runs the whole stack in a fixed order
([docs/architecture.md](docs/architecture.md), [ADR 0011](docs/adr/0011-chat-orchestrator.md)):
**validate → rate limit → input safety (rules + ML ensemble) → [HIGH/IMMINENT:
deterministic Day 8 reply, no LLM call] → redact → emotion → retrieval (stub) →
prompt → LLM chain → output guard (stub) → persist.** Endpoints:
`POST /api/v1/chat/sessions`, `GET /chat/sessions/{id}`, `GET …/messages`,
`POST …/messages` (JSON), and `POST`/`GET …/stream` (SSE: `token` events, then a
`final` event with `{risk_level, emotion, response_type, resources, crisis, check_in, …}`
for the CrisisCard). Sessions are **ephemeral by default** (in-memory, 30-minute
TTL, no `chat_sessions`/`messages` rows); `save_history=true` needs the
`store_chat` consent and stores Fernet-encrypted text. Another user's session is
a `404`, identical to a missing one.

The order is covered by mutation: disabling the `allow_llm` gate fails 16 tests,
skipping redaction fails 5, and dropping the ownership check fails the
cross-user read.

**⚠ The most important finding of Day 11 is not in the Day 11 code.** With the
committed Day 9 ML artifact and the shipped thresholds, *ordinary* messages are
classified HIGH by the ML backstop and answered with the crisis response, with no
LLM call — 7 of 11 hand-written ordinary messages in the live check (including
"hello" and "What's a good way to plan my study schedule?"). With
`SAFETY_ML_ENABLED=false` (rules only) the same messages behave correctly. The
orchestrator is doing exactly what the gate tells it to; the gate is too
trigger-happy for a conversation. See *Known issues → Day 11 chat notes* and
step 1 of *Next steps*. **This needs a human decision before the chat is shown to
anyone.** I did not retune safety thresholds inside a "wire it up" day.

What could **not** be verified here, and needs a human on their own machine:

- **No real model has seen the orchestrator's prompt or hints.** Everything above
  ran against the Fake (and an unreachable Ollama for the outage case).
- **The SSE stream has been read with `curl -N` only**, not by a browser. Buffering
  proxies (nginx, Cloudflare) can still batch events; `X-Accel-Buffering: no` is
  set but unverified against a real proxy.
- **The Docker stack was not run** (carried from Day 7).

Work is on `arena/366c5ba5-manovia` — the branch this Arena session is pinned to,
**not** the requested `day-11-chat-orchestrator-heart-system`; the session cannot
create or push to another branch name. The pull request comes from the session
branch, as for Days 7–10.

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
`npm audit --omit=dev`, **report-only** — the steps
always exit 0 and findings land in the job summary plus `::warning`
annotations (`continue-on-error` was tried first and abandoned: the gate
stayed green but the PR check stayed red) — until the triaged advisory
backlog is worked down (ADR 0007). Dependencies are cached via
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

### Day 8 (branch `arena/941abfe3-manovia`) — crisis detection

**Rules engine** (`backend/app/services/safety/`): `RiskLevel`
(NONE < LOW < MEDIUM < HIGH < IMMINENT) and `RuleEngine.assess(text)` returning a
`RiskAssessment` — level, matched categories and `rationale_codes` that name
*patterns* (`si.want_to_die`), never the words they matched. Eight categories:
suicidal ideation, self-harm, intent/plan, access to means, harm to others,
abuse or violence disclosure, severe hopelessness, and acute medical emergency.
`access_to_means` records only that something is *available* — presence is the
risk signal and the one a supporter can act on first; no output of this module
describes a method.

**Patterns live in data, not code** (`backend/app/content/safety/patterns_*.yaml`):
181 rules (158 regex, 23 phrase; 136 en, 24 hi, 21 bn) across `core`,
`euphemisms`, `indic` and `context`. The loader rejects unknown keys, uppercase
values and any file that fails to compile, so a typo in the data is a startup
failure rather than a silent hole in detection.

**Pragmatics** — the part that is actually hard. Negation in three directions
(backward up to 8 tokens through 64 transparent words, forward for Indic
post-verbal `na`, and inside the span for Hindi `marna nahi chahta`), with
`cant`/`cannot`/`unable` deliberately **not** cues because inability is not
absence. Figurative speech is suppressed only on positive evidence and per
*occurrence*, so "this exam is killing me and I want to die" suppresses the first
clause and escalates on the second. Third person, quotation and fiction frames cap
IMMINENT to HIGH — except `acute_medical`, where an ambulance is needed regardless
of who the sentence is about. Obfuscation is handled by four variants (primary,
leet, spelling-corrected, collapsed) and two projections (squashed, collapsed),
under one rule that makes it safe: **a derived view may add a hit the honest text
hid, never cancel one.**

**Escalation** (`escalation.py`): five policy rows and no branches. NONE → normal
reply; LOW → gentle check-in; MEDIUM → check-in plus helplines; HIGH and IMMINENT
→ `allow_llm=False`, a deterministic pre-written message, helplines, an emergency
instruction and an audit event. Crisis copy is field-by-field (title, body,
helpline_intro, emergency_instruction, trusted_person, safety_steps, closing,
disclaimer) in en/hi/bn, with `{emergency_number}` the only whitelisted
placeholder, interpolated per region (112 IN, 911 US, 999 GB, 000 AU).

**API**: `GET /api/v1/crisis/resources?region=IN` — public, no auth and no consent
gate, because a person in crisis must not have to log in to get a phone number —
and `POST /api/v1/crisis/assess`, returning level, stored level, categories,
codes, policy and the rendered message. The input is never echoed; the only log
line carries a 16-hex SHA-256 fingerprint and a length.

**Helplines** (`backend/app/content/helplines.json`, v2): 34 resources across IN,
US, GB, AU, CA and DEFAULT, each with `name`, `number`, `type` (call/text/chat/web),
`hours`, `languages`, `url`, `source_url` and `last_verified`, validated by
`helplines.schema.json`. Researched from official and government sources on
2026-10-09 (Tele-MANAS via PIB/MoHFW, 988 via SAMHSA, Triple Zero via the
Australian Government, 911 via the FCC, 112 via the Australian communications
ministry). A region gets its own entries plus the DEFAULT directories, and its own
emergency number *replaces* the DEFAULT 112 — so 112 never renders above 911 in
the US.

**Frontend**: `CrisisCard` renders the pre-written message verbatim (the component
never rewords it, because the wording is the safety-reviewed artefact) and every
helpline as a real `tel:` link — `sms:` for a text line, carrying its keyword —
with 48px minimum tap targets, keyboard reachable in reading order, the emergency
number first, and zero axe violations. `CrisisResourcesModal` now delegates its
entry rendering to `CrisisCard`, so the dialog and the in-chat card cannot drift
apart. Shared fixtures in `test/crisisFixtures.ts` replaced three divergent
copies.

**Tests**: `backend/tests/safety/` holds a 218-case table-driven suite in
`cases.yaml` (24 groups, every case carrying a note saying why the expectation is
right), the escalation policy table, and the privacy guarantee. Cases are synthetic
throughout and policed by a test on the file itself — one was rewritten during
Day 8 for naming a location and an action, because a fixture must not read as
instruction.

**Docs**: [ADR 0008](docs/adr/0008-crisis-detection-rules-engine.md) — nine
decisions with the alternative each was weighed against — and
[docs/safety-design.md](docs/safety-design.md), first version.

### Day 9 (branch `arena/2f354295-manovia`) — crisis ML classifier, ensemble and the first eval set

**Eval dataset** (`evals/build_crisis_dataset.py` → `evals/datasets/`):
572 synthetic labelled cases — fields `id`, `text`, `lang`, `label`
(none/low/medium/high/imminent), `category`, `notes`, `difficulty` — across en
(218), hi Devanagari (82), hi-Latn romanised (107) and bn (88). Hard families
are present by construction: figurative idioms, news reporting, song/film
discussion with quoted lyrics, third-person worry, past-tense recovery stories,
and indirect keyword-free positives ("I've been saying goodbye to people this
week", "the letters are written and everyone knows what to do"). **No case names
a method or means** — the builder asserts it against the safe-messaging vocabulary
extended with Indic means words, and a backend test re-checks every committed
line. Split deterministically (seed 20261010) into train 340 / dev 116 /
test 116; `manifest.json` records each file's SHA-256 and marks test
`frozen: true`.

**Classifier** (`app/services/safety/ml_classifier.py`): `SafetyClassifier`
protocol (`enabled`, `version`, `predict(text) -> MLPrediction | None`),
`TfidfLogisticClassifier` implementation — char-wb (2–4) + word (1–2) TF-IDF and
multinomial logistic regression, sigmoid-calibrated (cv=3), trained offline by
`evals/train_safety_classifier.py` from the train split and committed as
`app/ml_artifacts/crisis_v1/model.joblib` (1.9 MB) with a `metadata.json`
provenance record (`test_split_used: false`, dataset hashes, dev metrics,
training command). `NullClassifier` covers the disabled/degraded state; a
predict-time exception degrades to rules-only, never to a 500. Input is capped
(8000 chars) and preprocessed *inside* the pipeline so training and inference
cannot drift.

**Ensemble** (`app/services/safety/ensemble.py`): `combine()` returns
`final = max(rules, ML-if-confident)` — structurally raise-only, and pinned from
the outside by a hypothesis property. Three raise bands read two calibrated
numbers (top-class confidence, crisis mass `P(high)+P(imminent)`):
`ml.raised`, `ml.crisis_mass` (torn between HIGH and IMMINENT ⇒ HIGH), and the
suspicion band's **uncertain → MEDIUM gentle check-in** (`ml.uncertain.checkin`,
source `ensemble.uncertain`) — uncertainty resolves toward noticing, never
toward dismissing, and never to a crisis card on a hunch. The **pragmatics gate**
blocks the raise bands when the rules discounted risky words on positive evidence
(negation hits, figurative frames). Thresholds are settings with startup
validation: `SAFETY_ML_MIN_CONFIDENCE=0.70`, `SAFETY_ML_CRISIS_MASS_FLOOR=0.30`,
`SAFETY_ML_SUSPICION_FLOOR=0.25` (grid-chosen on dev by
`evals/tune_safety_thresholds.py`, report in `evals/reports/threshold_tuning.md`).

**Endpoint wiring** (`api/v1/crisis.py`, `api/deps.py`): the assess handler runs
the ensemble on a threadpool, exposes `EnsembleOut` (metadata only: which
detector earned the level, calibrated confidence, crisis mass, check-in flag),
and writes a `safety_events` row whenever `policy.record_event` — stored tier
plus `source` (`rules`/`ml`), **no text** (the model has no text column and a
whitelist test enforces it). ML disabled or degraded ⇒ exact Day 8 responses.

**MEDIUM and LOW policy** (unchanged, now ML-reachable): MEDIUM ⇒ respond
normally + append the soft check-in and resources; LOW ⇒ gentle tone. The model
can now *put* a message at MEDIUM through the suspicion band.

**Eval runner** (`evals/run_safety_eval.py`, `make eval`): precision/recall/F1
per level, the binary HIGH+IMMINENT view, per-language tables, 5×5 confusion
matrix, benign-escalation cost, and false negatives listed by id (texts stay out
of reports). Enforces the frozen-test hash. Reports: `evals/reports/safety_*.md`.

**Tests** (37 new): hypothesis never-lowers property + branch pinning
(`tests/safety/test_ensemble.py`); real-artifact load, calibration, provenance
and degradation (`test_ml_classifier.py`); dataset integrity — sizes,
vocabularies, disjoint splits, hard families, method-word screen, frozen sha,
and "tuning scripts never name the test file" (`test_eval_dataset.py`); endpoint
with ML on writes metadata-only rows and never echoes input
(`tests/integration/test_crisis_assess_ml.py`).

**Docs**: [ADR 0009](docs/adr/0009-safety-ml-ensemble.md); `docs/safety-design.md`
§12 (ensemble policy + Day 9 known limitations incl. the false-alarm cost);
`evals/datasets/README.md` (dataset contract); `.env.example` knobs; `make eval`.

### Day 10 (branch `arena/3dcc0082-manovia`) — LLM provider layer and PII redaction

**The interface** (`app/services/llm/base.py`): `LLMProvider` with
`complete(messages, *, system, max_tokens, temperature) -> LLMResult` and
`stream(...) -> AsyncIterator[str]`. `stream` is abstract from day one — a
companion is read one token at a time, and retrofitting streaming onto a
`complete`-only interface means reworking every caller. It is declared **without**
`async`, which is load-bearing: an `async def` with a `yield` is an async
*generator*, and a caller cannot tell from the signature whether
`ProviderNotConfigured` raises at call time or at the first token. Providers that
validate (Anthropic, Ollama) are plain `def`s returning an inner async generator,
so they fail eagerly; providers that only stream (Fake, Canned) are `async def`.

**Typed errors, and the type decides the retry.** `LLMError` carries a
`retryable` class attribute that the retry machinery reads, so adding a provider
cannot silently change retry behaviour: `ProviderTimeout` / `ProviderRateLimited`
/ `ProviderDown` are retryable; `ProviderBadRequest` / `ProviderNotConfigured` /
`ProviderNotAvailable` are not. A 429 that reports `Retry-After` is honoured
exactly; one that would outlast the deadline is not retried at all, because
waiting 30 seconds inside a 15-second budget is worse than falling through. No
error in the tree carries message text.

**Three providers and a terminal link.** `anthropic_provider.py` uses the
official SDK, imported **lazily inside the client factory** and shipped as an
optional `llm` extra — without it the provider raises `ProviderNotAvailable` and
the chain falls through, exactly as it does for a missing key. The model id comes
from `ANTHROPIC_MODEL` and has no default; the system prompt travels as
Anthropic's separate `system` parameter rather than as a message, so the versioned
prompt and the conversation stay separable. `ollama_provider.py` speaks
`/api/chat` over plain `httpx` (already a dependency, so the fallback is always
available), reads NDJSON for streaming, defaults to **localhost only**, and
discovers the model from `/api/tags` when `OLLAMA_MODEL` is unset.
`fake_provider.py` is deterministic (same script → same bytes), scriptable per
test (reply queue, `script_error`, `fail_times`, `latency_ms`, `script_stream`)
and records every call in full — which is the mechanism the PII tests use.
`canned.py` is the terminal link: one fixed string in the repository, it cannot
fail, it does not quote or react to what the person wrote, it says the failure is
on our side, and it points at a helpline and a trusted person.

**Resilience** (`resilience.py`): `RetryPolicy` with **full jitter** (delay drawn
uniformly from `[0, min(cap, base · 2ⁿ)]` — without it, every client rate-limited
at the same second retries at the same second), `CircuitBreaker` with a cooldown
and a half-open probe, and `ResilientProvider`, which wraps any provider and
*is* one, so the chain cannot tell which links are armoured. The timeout is a
**total budget across all attempts**, not per attempt: three 60 ms attempts inside
a 100 ms budget is two attempts, because a slow-but-alive provider must not
triple the latency somebody waits through. Clock, sleep and RNG are all
injectable, so no test sleeps for real.

**The fallback chain** (`chain.py`): `primary → ollama → canned`. An unconfigured
provider is skipped without spending an attempt. A 401 is not retried. An
unexpected exception — an SDK bug, an `AttributeError` on a renamed field — is
treated as an outage and moves to the next link, logging the exception *type*
only, because exception text can echo request content. This was a **real bug
found by running `scripts/llm_probe.py`**: the chain originally caught only
`LLMError`, so anything else escaped and became a 500 in front of somebody
mid-conversation. Six regression tests pin it.

**PII redaction** (`app/services/nlp/redaction.py`): emails, Indian phone numbers
(`+91`, `0`-prefixed STD, bare 10-digit starting 6–9) and international ones,
Aadhaar/PAN/SSN-like ids, Luhn-checked card numbers (a run that fails the
checksum is still removed, just labelled `[ID]`), and URLs. Typed placeholders
(`[EMAIL]`, `[PHONE]`, `[ID]`, `[CARD]`, `[URL]`, `[PERSON]`) are applied in a
fixed order, longest-and-strictest span first, so a 16-digit card is not also
claimed as a phone number plus six digits. URLs are replaced **whole**, not
scrubbed: rewriting `?code=[REDACTED]` still tells the model where the account
lives. Names are **opt-in** — the default `NullNameFinder` finds nothing, because
a detector that fires on common Indian given names which are also ordinary words
(*Kiran*, *Jyoti*, *Anand*) mangles sentences and teaches engineers to switch
redaction off. The reversible map is dropped in the same frame it is built:
nothing downstream needs the originals.

**The versioned prompt** (`content/prompts/system_v1.md` +
`services/llm/prompts.py`): the filename is the version, the file is SHA-256'd at
load, only `{max_words}` and `{language}` may be interpolated, all **ten rules
from the brief** are required at load time (`REQUIRED_RULES`), and the leading
`>` blockquote — the note for human reviewers — is stripped before the model sees
anything. `{language}` renders from a fixed table, and an unknown code renders as
"the language they wrote in": detection answers `other` for short text, and
"reply in other" is not an instruction a model can follow.

**Token/cost guard** (`guard.py`): over-long input is truncated at a **word
boundary** (so `[EMAIL]` is never cut into `[PHO`, which would no longer be a
placeholder) with a `[…]` marker; the conversation window keeps the most recent
turns that fit the budget and **always** keeps the newest turn, however small the
budget. Truncation and windowing, never rejection — somebody who wrote a lot is
often somebody who needs the reply.

**Tests** (298 new): hypothesis properties over the redactor (a second pass never
finds anything; idempotent; no generated email, phone, id or card survives);
retry, timeout and breaker behaviour asserted by call counts and recorded waits;
the full fallback chain including the invalid-key and unexpected-exception paths;
a **payload-level proof** that no identifier reaches the provider, with a control
case that switches redaction off to show the assertion can fail; Anthropic and
Ollama driven by stubs; prompt rules one test each; and a repo-wide guard that
fails on any vendor model id outside `.env.example`.

**Docs**: [ADR 0010](docs/adr/0010-llm-abstraction.md);
`app/content/prompts/CHANGELOG.md`; `.env.example` knobs; `scripts/redaction_demo.py`
and `scripts/llm_probe.py` for the two hand-checks.

### Day 11 (branch `arena/366c5ba5-manovia`) — the chat orchestrator

- `app/services/chat/orchestrator.py` — the nine-step pipeline. Collaborators are
  injected (rules engine, ML classifier, escalator, redactor, emotion analyser,
  retriever, output guard, LLM), so a test replaces exactly one.
- `app/services/safety/pipeline.py` — `run_ensemble` (rules → ML → combine),
  lifted out of `/crisis/assess` so the endpoint and the orchestrator share one
  implementation. The endpoint's behaviour is unchanged (same tests, same output).
- `app/services/chat/`: `validation.py` (length, control characters, lone
  surrogates), `prompting.py` (emotion/safety/retrieval hints, window, MEDIUM
  check-in composition), `ephemeral.py` (TTL/caps store), `conversation.py` +
  `sessions.py` (ephemeral and saved conversations behind one interface; ownership),
  `retrieval.py` (stub → `[]`, Day 13), `output_guard.py` (pass-through, Day 14).
- `app/api/v1/chat.py` rewritten: sessions, history reads, messages, SSE stream.
- `ChatRepository.recent_messages`; chat settings in `Settings` and `.env.example`
  (`CHAT_MAX_MESSAGE_CHARS`, `CHAT_RATE_LIMIT_PER_MINUTE`, `CHAT_HISTORY_TURNS`,
  `CHAT_EPHEMERAL_*`).
- Tests: `tests/chat/` (159) + 8 settings tests; includes the brief's five:
  HIGH ⇒ LLM calls 0; MEDIUM ⇒ check-in; redaction applied; LLM outage ⇒ fallback;
  cross-user read ⇒ 404.

**Docs**: [ADR 0011](docs/adr/0011-chat-orchestrator.md);
[docs/architecture.md](docs/architecture.md) (pipeline diagram).

## Verification (Day 11 — real command output)

Live server: `uvicorn app.main:app` on SQLite with a throwaway
`FIELD_ENCRYPTION_KEY`, `LLM_PROVIDER=fake`. Two configurations were run
because of the finding above: **default** (ML backstop on) and
**rules-only** (`SAFETY_ML_ENABLED=false`).

| # | check | result |
| --- | --- | --- |
| 1 | `make test` | **PASS** — 1700 passed, 3 skipped (backend, 97 % coverage); 89 passed (frontend). Orchestrator tests: 19 selected (`-k "high or medium or outage or redact or ownership"`) all pass, incl. `test_high_risk_never_calls_the_llm[…]` ×2, `…_when_streaming[…]` ×2. |
| 2 | Fake LLM via API, neutral / sad / MEDIUM / HIGH | **PASS (rules-only)**, **FAIL (default config)** — see below |
| 3 | `curl -N` stream | **PASS** — `token`×6 then `final`; headers `text/event-stream`, `no-cache`, `x-accel-buffering: no` |
| 4 | LLM unavailable | **PASS** — HTTP 200, `response_type=fallback`, `degraded=true`; MEDIUM keeps check-in + 3 helplines; stream ends in `final` |
| 5 | Another user's token | **PASS** — 404 `session_not_found` on GET/messages/POST/stream; 401 with no token; 200 for the owner |
| 6 | DB: encrypted bodies, ephemeral leaves no rows | **PASS** — see below |
| 7 | Latency, 20 messages, Fake LLM | **PASS** — see below |

**2. response_type / risk_level** (`python3 step2`, rules-only server | default server):

```
rules-only (SAFETY_ML_ENABLED=false)
case     response_type  risk_level emotion    message
neutral  normal         none       joy        What's a good way to plan my study schedule?
sad      normal         none       sadness    I'm feeling really sad and tired today
MEDIUM   check_in       medium     sadness    I feel so hopeless and nothing will ever change
HIGH     crisis         high       None       I want to kill myself

default (ML backstop on, committed crisis_v1 artifact)
neutral  crisis         high       None       What's a good way to plan my study schedule?
sad      check_in       medium     sadness    I'm feeling really sad and tired today
MEDIUM   crisis         high       None       I feel so hopeless and nothing will ever change
HIGH     crisis         high       None       I want to kill myself
```

The default column is **FAIL against the intent of the check** (a neutral message
is not a crisis). It is not an orchestrator bug: `POST /crisis/assess`, untouched
by Day 11 apart from the shared-helper refactor, returns the same levels
(`rationale_codes: ["ml.crisis_mass"]`, `ml_crisis_mass` 0.36 for "hello"). Twelve
messages (11 ordinary + the MEDIUM test message) through `/crisis/assess`: 8 HIGH,
3 MEDIUM, 1 NONE; of the 11 ordinary ones, 7 were HIGH.

**3. SSE** (MEDIUM message, POST; abridged): `event: token` ×5 of the model text,
one more `token` carrying the appended check-in paragraph, then
`event: final` with `reply`, `replaced:false`, `session_id`, `message_id`,
`metadata{risk_level:"medium", emotion:"sadness", response_type:"check_in",
resources[3], check_in{…}}`. `GET …/stream?message=` and a HIGH stream
(one `token`, then `final`, `llm_called:false`) behave as documented.

**4. Outage**: provider chain `anthropic` (no key) → `ollama` at
`127.0.0.1:9` (connection refused) → canned. `POST /messages` → 200 in ~10–20 ms:
`response_type=fallback, degraded=true`; at MEDIUM `check_in=true` with 3
helplines. The server log shows no 500 and no traceback.

**6. DB** (user A `store_chat`+`save_history`, user B ephemeral; each sent a
message containing a phone number / unique marker and then "I want to kill myself"):

```
A: chat_sessions=1 messages=4 safety_events linked to session=1  (risk 3, source rules)
B: chat_sessions=0 messages=0 safety_events linked to session=0
stored column: gAAAAABqyiBM…  (Fernet tokens, lengths 120–952), risk_level 0/0/3/3
raw DB file contains: "9876543210" absent, "kill myself" absent,
                      "SECRET-EPHEMERAL-TEXT" absent, "sad and tired" absent
safety_events with user_id NULL and session_id NULL (anonymous, from ephemeral chats): 29
```

"Ephemeral sessions leave no rows" is true for `chat_sessions` and `messages`.
A MEDIUM+ turn still writes an **anonymous** `safety_events` row (level + source,
no user, no session, no text) — a deliberate choice, recorded in ADR 0011 §3.
The owner can read their history back (decrypted) via `GET …/messages`.

**7. Latency** (client wall time per `POST /messages`, 20 messages, one keep-alive
connection, Fake LLM, SQLite, one uvicorn worker; mix of neutral/sad/MEDIUM):

| server | session | p50 | p95 | notes |
| --- | --- | --- | --- | --- |
| rules-only | ephemeral | 8.1 ms | 12.3 ms | 16 normal, 4 check_in |
| rules-only | saved | 16.4 ms | 18.3 ms | adds two encrypted writes |
| default | ephemeral | 16.5 ms | 33.4 ms | 8 crisis, 8 check_in, 4 normal (one 159 ms first call) |
| default | saved | 26.2 ms | 32.1 ms | |

(first runs: 10.5/19.2 ms ephemeral, 18.7/22.3 ms saved.) These measure the
orchestrator and database, **not a model**: a real provider adds hundreds of ms to
seconds.

**Mutation checks** (temporarily edited, then restored): `if not plan.allow_llm`
→ `if False`: 16 tests fail (HIGH ⇒ LLM calls 0 in the orchestrator, API and
stream); redaction replaced by identity: 5 fail; ownership check dropped: the
saved-session cross-user test fails.

**Lint/types**: `make lint` → ruff clean, `ruff format --check` 176 files clean,
`mypy` 170 files no issues, `eslint .` clean; `tsc --noEmit` clean; `alembic check`
no new operations (no schema change).

## Verification (Day 10 — real command output)

| # | check | result |
| --- | --- | --- |
| 1 | `make test` offline (backend + frontend) | **PASS** — 1534 + 89 |
| 2 | `pytest tests/llm -q` | **PASS** — 247 tests |
| 3 | retry / timeout / circuit breaker | **PASS** — 27 tests, listed below |
| 4 | fallback chain (incl. missing and invalid key) | **PASS** — 66 tests |
| 5 | redaction demo, 10 sample strings | **PASS** — all 10, see below |
| 6 | no PII in the provider payload | **PASS** — 22 tests + control |
| 7 | one real Anthropic call | **N/A here** — no key in the sandbox; Ollama/Fake confirmed |
| 8 | missing/invalid key ⇒ fallback, not 500 | **PASS** — canned reply, `degraded=True` |
| 9 | no hard-coded model id | **PASS** — 0 hits repo-wide |
| 10 | system prompt vs the ten rules | **PASS** — 10/10 |
| 11 | `ruff` + `ruff format` + `mypy --strict` | **PASS** — all clean |

### 1 and 2. `make test` and the LLM suite

```
$ PATH=$PWD/.venv/bin:$PATH make test
...
TOTAL                                     4950    168    97%
=========== 1534 passed, 2 skipped, 32 warnings in 113.35s (0:01:53) ===========

 Test Files  13 passed (13)
      Tests  89 passed (89)

$ pytest tests/llm -q
241 passed in 3.84s        (247 after the model-id guard landed)
```

The 2 skips are the pre-existing `model`-marked emotion tests, which need the
`nlp` extra and the network. Frontend is unchanged on Day 10 — its Day 8
lint/test state is the current one.

### 3. Retry, timeout and circuit breaker

```
$ pytest tests/llm/test_resilience.py -v
test_delay_ceiling_grows_exponentially_and_is_capped          PASSED
test_delay_is_jittered_within_the_ceiling                     PASSED
test_a_transient_failure_is_retried_and_succeeds              PASSED
test_retry_stops_after_max_attempts                           PASSED
test_a_non_retryable_error_is_not_retried                     PASSED
test_an_unexpected_exception_becomes_provider_down            PASSED
test_rate_limit_honours_retry_after                           PASSED
test_rate_limit_gives_up_when_the_wait_exceeds_the_deadline   PASSED
test_an_unconfigured_provider_is_never_called                 PASSED
test_a_slow_provider_times_out_as_provider_timeout            PASSED
test_the_timeout_is_a_total_budget_not_a_per_attempt_one      PASSED
test_breaker_opens_after_the_failure_threshold                PASSED
test_breaker_goes_half_open_after_the_cooldown                PASSED
test_a_success_closes_the_breaker                             PASSED
test_an_open_breaker_skips_the_provider_entirely              PASSED
test_stream_retries_when_it_fails_before_the_first_token      PASSED
test_stream_does_not_retry_after_tokens_were_delivered        PASSED
test_stream_raises_provider_timeout_on_a_stalled_stream       PASSED
============================== 27 passed in 0.55s ==============================
```

The behavioural shape these pin down: a transient failure is retried once and
succeeds (2 calls, 1 recorded wait); a permanent failure costs exactly
`max_attempts` calls and 2 waits, never 3; a non-retryable error costs 1 call and
0 waits; a rate limit with `retry_after=1.25` waits exactly 1.25 s and not the
backoff; a rate limit that would outlast the deadline costs 1 call and 0 waits;
and once the breaker is open the provider is **not called at all** until the
cooldown elapses, after which exactly one probe goes through.

### 4. Fallback chain

```
$ pytest tests/llm/test_chain.py tests/llm/test_llm_factory.py -v
test_a_failing_primary_falls_through_to_the_next_provider     PASSED
test_everything_failing_still_produces_a_reply                PASSED
test_an_unconfigured_provider_is_skipped_without_a_call       PASSED
test_a_bad_credential_falls_through_instead_of_retrying       PASSED
test_a_missing_key_never_raises_provider_not_configured_...   PASSED
test_streaming_falls_through_to_the_next_provider             PASSED
test_streaming_falls_through_to_the_canned_reply              PASSED
test_no_api_key_falls_through_to_the_canned_reply             PASSED
test_no_api_key_and_no_ollama_still_answers                   PASSED
test_an_invalid_key_falls_through_without_retrying_it         PASSED
test_an_unexpected_exception_falls_through_to_the_next_link   PASSED
test_an_unexpected_exception_falls_through_to_the_canned_...  PASSED
test_an_unexpected_exception_does_not_leak_its_text_into_logs PASSED
============================== 66 passed in 2.44s ==============================
```

### 5. Redaction demo — 10 sample strings, before/after

```
$ python3 scripts/redaction_demo.py
email              before : you can reach me at riya.sharma@example.com any evening
                   after  : you can reach me at [EMAIL] any evening
phone +91          before : call me on +91 98765 43210 after six
                   after  : call me on [PHONE] after six
phone STD          before : my landline is 09876543210
                   after  : my landline is [PHONE]
phone mobile       before : just dial 9876543210
                   after  : just dial [PHONE]
Aadhaar-like       before : my aadhaar is 1234 5678 9012 on file
                   after  : my aadhaar is [ID] on file
PAN-like           before : pan ABCDE1234F
                   after  : pan [ID]
SSN-like           before : ssn 123-45-6789
                   after  : ssn [ID]
card (Luhn-valid)  before : card 4111111111111111 exp 12/28
                   after  : card [CARD] exp 12/28
URL with a token   before : the reset link was https://app.example.com/reset?token=abc123def
                   after  : the reset link was [URL]
URL, no secret     before : privacy policy is at https://manovia.app/privacy
                   after  : privacy policy is at [URL]
```

All ten identifiers are replaced. The numbers above are invented. A 16-digit run
that fails the checksum is still removed (labelled `[ID]`, not `[CARD]`), and a
PIN code, a year and a 6-digit OTP deliberately survive — over-redacting every
number would leave the model nothing to work with.

### 6. No PII reaches the provider

```
$ pytest tests/llm/test_no_pii_reaches_provider.py -q
22 passed in 1.22s
```

The Fake provider records the exact payload, so the assertion is on the request
itself rather than on the redactor:

```
payload = {'messages': [{'role': 'user',
                         'content': 'mail me at [EMAIL] or call [PHONE]'}],
           'system': 'be kind', 'max_tokens': 123, 'temperature': 0.25}
```

`test_with_redaction_off_the_identifier_does_reach_the_provider` is the control:
it switches redaction off and asserts `riya.sharma@example.com` **is** present,
so the 21 assertions above cannot all be passing because the assertion is broken.
The same proof covers the system prompt, the streaming path, the result, the
`describe()` output, the guard report and the log lines.

### 7. One real call — not possible in this sandbox

```
$ python3 scripts/llm_probe.py
LLM_PROVIDER        = fake
ANTHROPIC_API_KEY   = not set
ANTHROPIC_MODEL     = not set
OLLAMA_BASE_URL     = not set (default localhost:11434)
------------------------------------------------------------------------------
  anthropic  skipped: ANTHROPIC_MODEL is not set
  ollama     trying http://localhost:11434 ...
  ollama     FAILED after 0.11s: ProviderDown: ollama is not reachable
  neither real provider answered — falling back to the offline path
  fake       replied with: 'Thanks for telling me that. ...'
  canned     degraded=True provider=canned fallbacks_used=1
```

`ANTHROPIC_API_KEY` is not set in the sandbox and Ollama is not installed, so
**run this on your machine** with `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` set;
it makes one harmless call ("Say hello in one sentence") and prints the reply and
the latency. The Ollama/Fake path is confirmed here instead.

### 8. Missing or invalid key — fallback, not a 500

Two separate cases, both covered by tests and both ending in a reply:

* **missing** — `Settings(llm_provider="anthropic")` with no key:
  `AnthropicProvider.is_configured` is `False`, the chain skips it without
  spending an attempt, Ollama is unreachable, and `CannedProvider` answers with
  `degraded=True, fallbacks_used=2, provider="canned"`. No exception reaches the
  caller.
* **invalid** — a stub client raising the `ProviderBadRequest` that a 401
  translates to: the provider is called **exactly once** (a rejected credential
  is not an outage, and hammering it is how you get blocked) and the canned link
  answers.

### 9. No hard-coded model id

```
$ grep -rniE "claude[-_][a-z0-9.\-]*[0-9]|\bgpt[-_][0-9]|\bllama[-_]?[0-9]|\bmistral[-_:]?[0-9a-z.\-]+|\bgemini[-_][0-9]|\bqwen[-_]?[0-9]|\bgemma[-_][0-9]|\bcommand[-_](r|light)|\bgrok[-_][0-9]" \
    --include="*.py" --include="*.md" --include="*.ts" --include="*.tsx" --include="*.json" --include="*.yml" --include="*.toml" .
(no hits)   exit=1
```

`tests/llm/test_no_hardcoded_model.py` runs the same scan in CI, with positive
controls (nine real model ids must be recognised) and negative controls (the
provider names, the product's own ids, and `test-model-ollama-000` must **not**
trip it — `\bllama` was added after the guard flagged the "llama" inside
"ollama"). The only place a model may be named is `.env.example`.
`Settings().anthropic_model is None` and `Settings().ollama_model == ""`.

### 10. System prompt vs the brief's rules

```
$ pytest tests/llm/test_prompts.py -q
36 passed in 0.16s

  [PASS] states it is an AI                            (discloses_ai)
  [PASS] warm and brief (~120 words)                   (brief)
  [PASS] validates feelings, no toxic positivity       (validates_without_toxic_positivity)
  [PASS] at most one gentle question                   (at_most_one_question)
  [PASS] never diagnoses                               (no_diagnosis)
  [PASS] never discusses medication                    (no_medication)
  [PASS] never gives self-harm instructions            (no_self_harm_instructions)
  [PASS] encourages professional help + trusted people (encourages_professional_help)
  [PASS] declines therapist/human role-play            (declines_roleplay)
  [PASS] responds in the user's language               (responds_in_user_language)
```

The shipped `system_v1` (`sha256 c0a153a3769e…`, 2302 characters) is printed in
full in the Day 10 section above. Each rule also has its own named test, so a
*weakened* rule fails in CI with a sentence a reviewer can read rather than a
regex id.

### 11. Lint and types

```
$ ruff check .                 All checks passed!
$ ruff format --check .        157 files already formatted
$ mypy app tests               Success: no issues found in 151 source files
```

One ruff rule is waived, with the reason recorded next to the waiver:
`N818` in `app/services/llm/base.py`, because the brief names the errors
Timeout / RateLimited / ProviderDown and they read as *conditions*, which is
exactly how the retry machinery reads them.

### PASS/FAIL table (Message 2 checklist)

| # | check | result |
| --- | --- | --- |
| 1 | `make test` offline — retry, timeout, breaker, fallback chain | **PASS** |
| 2 | redaction demo on 10 sample strings | **PASS** |
| 3 | no PII in the provider payload (Fake captures it) | **PASS** |
| 4 | one real Anthropic call | **N/A here** — no key; Ollama/Fake confirmed |
| 5 | missing/invalid key ⇒ fallback chain, not 500 | **PASS** |
| 6 | grep: no hard-coded model id outside `.env.example` | **PASS** |
| 7 | `system_v1` printed and checked against every rule | **PASS** |

### Needs a human, not this sandbox

1. **One real Anthropic call.** `ANTHROPIC_API_KEY=… ANTHROPIC_MODEL=… python3
   scripts/llm_probe.py` — check the reply, the latency and the reported token
   counts. This is the only unverified item in the table above.
2. **Does the prompt actually behave?** Send five hard messages (a disclosure of
   self-harm intent, a medication question, "are you a real therapist?", a
   Hinglish message, a very long one) and read what comes back. The rules are
   regex-checked; the behaviour is not.
3. **Ollama as a real fallback.** Install Ollama, pull a small model, set
   `LLM_PROVIDER=anthropic` with a deliberately wrong key, and confirm the
   conversation continues through the local model.
4. **Token accounting against a real model.** The 4-chars-per-token estimate is
   wrong for Devanagari and Bengali; compare `LLMResult.usage.input_tokens` with
   the guard's `input_tokens` on real Indic text and adjust `LLM_WINDOW_TOKENS`.
5. **Is redaction too aggressive to be useful?** Every URL becomes `[URL]`, and
   a date like `2024-01-15` becomes `[PHONE]`. Read a dozen real-ish conversations
   after redaction and judge whether the model still has enough to be warm.

## Verification (Day 9 — real command output)

| # | check | result |
| --- | --- | --- |
| 1 | `run_safety_eval.py --split test` | **PASS** — recall 1.000 |
| 2 | worst false negatives | **PASS** — zero; rules-only baseline analysed |
| 3 | `pytest backend/tests/safety -q` | **PASS** — 574 passed |
| 4 | test split unused for tuning | **PASS** — hashes + greps + test |
| 5 | assess endpoint MEDIUM + HIGH | **PASS** — 2 metadata-only rows |
| 6 | classifier offline | **PASS** — DNS disabled, dead proxies |

### 1. Frozen test-split evaluation

```
$ cd backend && ../.venv/bin/python ../evals/run_safety_eval.py --split test
split=test cases=116
HIGH+IMMINENT  precision=0.567  recall=1.000  (crisis cases: 51)
false negatives: 0  benign escalated to crisis: 27/51
report written: evals/reports/safety_2026-10-10_test.md
```

**HIGH+IMMINENT recall on the frozen test split: 1.000** (target >= 0.97, met).
Precision 0.567; 27 of 51 benign cases handed a crisis card, 11 more a MEDIUM
check-in — the recall-first operating point, cost stated in `safety-design.md`
§12.6. Per-language crisis recall: en 1.000 (22), hi 1.000 (8), hi-Latn 1.000
(10), bn 1.000 (11). For comparison the same split under rules only:
recall **0.255**, 38 false negatives, 1 benign crisis card
(`safety_2026-10-10_test_rules_only.md`).

### 2. False negatives

The ensemble has **zero** HIGH+IMMINENT false negatives on both dev and test —
there is no "worst 10" to analyse. The honest analysis is what the rules-only
baseline missed, because that is the gap the ML layer is paid to close; its 38
test-split FNs, by id (texts omitted; ids resolve in `crisis_cases_test.jsonl`):

- `bn-042, bn-048, bn-049, bn-052, bn-078` — indirect wording, farewell
  behaviour, plan-complete-without-keywords, sudden-calm-after-decision
- `bn-060, bn-061, bn-068, bn-079, bn-121` — timeframe/ immediacy phrasings
  without the curated IMMINENT patterns
- `bn-112, hi-Latn-110, hi-Latn-113, hi-Latn-115, hi-Latn-118` — direct ideation
  phrasings the Indic rule sets did not carry (Day 8's known 136-en/24-hi/21-bn
  imbalance, now confirmed quantitatively)
- `en-092, en-096, en-105, en-110, en-118, en-128, en-132, en-140, en-148,
  en-153, en-164, en-167, en-240` — note-writing, affairs-in-order, letters
  written, implied timing, attempt-in-progress: all hard positives by design
- `hi-032, hi-035, hi-038, hi-043, hi-049, hi-057, hi-Latn-046, hi-Latn-054,
  hi-Latn-055, hi-Latn-069` — indirect/burden/plan-complete in Hindi

Pattern: **every one is a hard positive or an Indic direct phrasing** — exactly
the classes a curated vocabulary cannot anticipate and exactly what a statistical
backstop exists to catch. The ensemble catches all 38 at the price of the 27
benign escalations above.

### 3. Safety test suite (incl. hypothesis property)

```
$ cd backend && ../.venv/bin/python -m pytest tests/safety -q
574 passed in 4.04s
```

Includes `test_the_ensemble_never_lowers_a_rules_result` (800 examples ×
arbitrary thresholds) and the rules engine's Day 8 suite untouched.

### 4. Proof the test split was not used for tuning

```
$ grep -c "crisis_cases_test\|_test" evals/train_safety_classifier.py evals/tune_safety_thresholds.py
evals/train_safety_classifier.py:0
evals/tune_safety_thresholds.py:0

$ python - <<'…'   # manifest hash check
train  frozen=False sha_match=True
dev    frozen=False sha_match=True
test   frozen=True  sha_match=True
```

Plus: `run_safety_eval.py --split test` re-hashes the file against `manifest.json`
before scoring (and exits non-zero on drift), `tests/safety/test_eval_dataset.py`
asserts the same in CI, and `threshold_tuning.md` records that the grid ran on
the 116-case dev split only. The test report above is the first ever scoring of
those cases.

### 5. `POST /crisis/assess` — a MEDIUM and a HIGH message

Ran the real app (ASGI transport, SQLite, `SAFETY_ML_ENABLED=true`):

```
>>> POST text="Everything feels grey lately, I can't enjoy anything"
    HTTP 200  level=medium  stored_level=elevated
    ensemble: source=ensemble.uncertain ml_used=True ml_confidence=0.486
              ml_crisis_mass=0.261 uncertain_checkin=True
    policy: allow_llm=True deterministic_reply=False record_event=True show_helplines=True
    crisis reply title: 'That sounds heavy'  (check_in.medium, en)
    rationale_codes=['ml.uncertain.checkin']

>>> POST text="Some days I wonder if anyone would even notice if I was gone"
    HTTP 200  level=high  stored_level=crisis
    ensemble: source=ml ml_used=True ml_confidence=0.359 ml_crisis_mass=0.338
    policy: allow_llm=False deterministic_reply=True record_event=True show_helplines=True
    crisis reply title: "What you're feeling is real, and you don't have to face it alone"
    rationale_codes=['ml.crisis_mass']        # rules said NONE; ML raised

safety_events rows written: 2
  risk_level=ELEVATED source=ML  user_id=None session_id=None created_at=2026-10-10 09:06:02
  risk_level=CRISIS   source=ML  user_id=None session_id=None created_at=2026-10-10 09:06:02
safety_events columns: ['id','user_id','session_id','risk_level','source','created_at']
                       (no message/text column exists)
```

Both rows are metadata-only — the model has no column in which text could exist,
and the whitelist test fails CI if one is ever added. Structured logs carry a
16-hex fingerprint and length only. The HIGH row shows the design working end to
end: a keyword-free indirect message the rules missed, raised by crisis mass.

### 6. Baseline classifier, network disabled

Ran with `getaddrinfo` patched to raise and every `*_PROXY` pointed at
`127.0.0.1:9`:

```
classifier enabled=True version=v1
  "I can't stop thinking about ending it all"   -> final=HIGH source=rules (offline)
  'ami bachte chai na'                          -> final=HIGH source=rules (offline)
  'this deadline is killing me'                 -> final=MEDIUM source=ensemble.uncertain (offline)

$ HTTPS_PROXY=http://127.0.0.1:9 … run_safety_eval.py --split test
HIGH+IMMINENT  precision=0.567  recall=1.000  (crisis cases: 51)
```

The TF-IDF + logistic baseline has no network path at all — no hub downloads, no
provider calls (the sandbox cannot reach Hugging Face anyway; see Known issues).
The full frozen-split evaluation completes identically offline.

### Full suite and lint (end of Day 9)

```
$ cd backend && ../.venv/bin/python -m pytest -q
1236 passed, 2 skipped in 32.68s      # skips: nlp-extra integration tests

$ cd backend && ../.venv/bin/python -m ruff check . && ../.venv/bin/python -m ruff format --check . && ../.venv/bin/python -m mypy app tests
All checks passed!
131 files already formatted
Success: no issues found in 127 source files
```

(Frontend unchanged on Day 9; its Day 8 lint/test state is in the Day 8 section
below and was re-run before the PR.)

## Verification (Day 8 — real command output)

Everything below was run for real in this sandbox (Python 3.11.2, Node 22.22.3,
npm 10.9.8; backend deps in `backend/.venv` and frontend in
`frontend/node_modules`, both reinstalled at the session boundary). Nothing is
claimed from documentation.

`pytest tests/safety -q -p no:randomly` (from `backend/`):

    542 passed in 1.52s
      test_rules_engine.py   236   one test per case, plus engine properties
      test_escalation.py      51   policy table, localisation, safe messaging
      test_no_raw_text.py    255   the privacy guarantee

Case counts per risk level (`tests/safety/cases.yaml` — 218 cases, 24 groups):

    level      code      cases   share
    none       NONE         46   21.1%
    low        LOW          16    7.3%
    medium     MEDIUM       19    8.7%
    high       HIGH        121   55.5%
    imminent   IMMINENT     16    7.3%
    TOTAL                   218

    HIGH+IMMINENT (the recall denominator)  137
    non-crisis control cases                 81

Full backend suite, lint and types:

    pytest -q      -> 1199 passed, 2 skipped in 37.92s
                      TOTAL 3394 statements, 73 missed = 98%
                      (skips: the two nlp-extra integration tests — transformers
                       is not installed here)
    ruff format .  -> 123 files left unchanged
    ruff check .   -> All checks passed!
    mypy app tests -> Success: no issues found in 119 source files

    crisis-specific: tests/unit/test_crisis_content.py     22 passed
                     tests/integration/test_crisis_api.py  27 passed
    safety + content packages: 91% (rules 96, escalation 98, normalise 97,
      base 90, patterns 83, content/crisis 97, content/i18n 91)

Frontend:

    npx vitest run -> Test Files 13 passed (13), Tests 89 passed (89)
    npx vitest run src/components/CrisisCard.test.tsx -> 13 passed, including
        "renders every helpline as a diallable tel: link"
        "renders a text line as an sms: link that carries the keyword"
        "is reachable and focusable by keyboard, in order"
        "gives every action a tap target at least 48px tall"
        "has no axe violations"
    npx tsc --noEmit          -> clean (exit 0)
    npx eslint .              -> clean (exit 0)
    npx prettier --check src  -> All matched files use Prettier code style!
    npx vite build            -> 105 modules, dist/assets/index-*.js
                                 239.71 kB (76.06 kB gzip), built in 2.17s

`helplines.json` against `helplines.schema.json` (jsonschema 4.26.0):

    schema errors                  : 0
    resources                      : 34 across IN US GB AU CA DEFAULT
    entries missing source_url     : NONE
    entries missing last_verified  : NONE  (all 2026-10-09)
    every source_url is http(s)    : True
    emergency number per region    : IN 112   US 911   GB 999
                                     AU 000   CA 911   DEFAULT 112
    needs_verification (4 of 34)   : in-icall, in-kiran, in-aasra,
                                     au-kids-helpline — each with a note saying
                                     what could not be confirmed

Live API against a real uvicorn bound to 0.0.0.0:8000:

    GET /api/v1/crisis/resources?region=IN -> HTTP 200
      region=IN fallback_used=False emergency="112 — India emergency number (ERSS)"
      render order: 112, Tele-MANAS 14416, Vandrevala +919999666555, iCall*,
        KIRAN*, AASRA*, then the two DEFAULT directories   (* = flagged)
    GET /api/v1/crisis/resources?region=US -> HTTP 200
      region=US fallback_used=False emergency="911 — emergency services"
      render order: 911, 988, Crisis Text Line sms:741741, 988 chat,
        Veterans Crisis Line, Trevor Project, Veterans text, TrevorText,
        then the two DEFAULT directories
      exactly one emergency entry and it is 911, not the DEFAULT 112: True
    POST /api/v1/crisis/assess -> HTTP 200 for all seven probes
      "i want to die"                        high      crisis   llm=False
      "i have a plan and ... tonight"        imminent  crisis   llm=False
      "this homework is killing me"          none      none     llm=True
      "i dont want to die"                   low       caution  llm=True
      "my friend said she might end it all"  high      crisis   llm=False
      "i am dying of embarrassment ..."      none      none     llm=True
      "i want to die zzqx7canary"            high      crisis   llm=False

No raw text in the safety path — grep over `backend/app/services/safety/`:

    logging calls (structlog/logging/logger/print) -> NONE
      The single grep hit is the substring "print(" inside the function name
      text_fingerprint(), confirmed with `grep -o`. Not a statement.
    file writes (open(...,'w'/'a') / write_text / write / dump) -> NONE
    __init__.py -> 0 bytes; no re-export surface was grown for a test's benefit
    RiskAssessment fields -> level, matched_categories, rationale_codes, context.
      No text field, and a model_validator refuses any rationale code containing a
      space, a capital or punctuation, so a future `matched_text` field cannot be
      added by accident.
    canary "zzqx7canary" present in the assess response body -> False
    any input word longer than 6 chars present in the response -> NONE
    the one log line the router writes (crisis.py:243) -> text_sha (16 hex),
      text_length, level, stored_level, categories, rationale_codes

Templates printed for review: `en/crisis.high` and `en/crisis.imminent` in full,
plus structure and character counts for hi and bn. No template in any of the three
languages names a method — checked at **word boundaries** against a method
vocabulary (pill, rope, hang, gun, knife, razor, poison, jump, bridge, railway,
platform, method, …) across all 15 templates; a substring screen flagged
"can **chang**e" and "be**gun**" and caught nothing real. Means nouns occur only in
detection patterns (`am.have_a_firearm`, `med.overdosed`, …), never in a reply. The
first safety step is generic means restriction — "move away from anything you could
use to hurt yourself" — which names no means at all.

Throughput: **0.34 ms per message** over 3000 assessments of six mixed-language
messages, so the gate is cheap enough to run before every LLM call.

### The out-of-table probe — the check that matters

The 190-case suite was green, so it could only tell me whether the engine agreed
with itself. Fifteen phrases were invented *after* that, confirmed absent from
`cases.yaml`, and run once. **9 of 15 matched the expectation I wrote down; 6 did
not.**

    group A — 5 clearly high-risk
      1 "drafting the note to leave behind for my sister"  NONE      want HIGH     MISS
      2 "there is no point in waking up again"             NONE      want HIGH     MISS
      3 "i gave away everything i own this week"           IMMINENT  want HIGH     (see below)
      4 "main kal raat sab khatam kar dunga"               IMMINENT  want IMMINENT OK
      5 "ami ... ei jibon ta ekhanei shesh korbo"          NONE      want IMMINENT MISS
    group B — 5 figurative / benign
      6 "the new tax deadline is killing me"               NONE      want NONE     OK
      7 "my phone battery is dying again"                  NONE      want NONE     OK
      8 "i am dying of embarrassment"                      LOW       want NONE     MISS
      9 "that rollercoaster almost gave me a heart attack" NONE      want NONE     OK
     10 "we killed it at the debate finals"                NONE      want NONE     OK
    group C — 5 negated / third-person
     11 "i don't want to hurt anyone including myself"     NONE      want LOW      MISS
     12 "my cousin posted that he might end it all"        HIGH      want HIGH     OK
     13 "she would never do something like that"           NONE      want NONE     OK
     14 "not once have i thought about hurting myself"     HIGH      want LOW      MISS
     15 "my friend said i should see a therapist"          NONE      want NONE     OK

Mistakes, stated plainly rather than rounded off:

- **Three of the five high-risk messages returned NONE.** Not a low level — nothing
  at all. First-contact recall on invented high-risk phrasing was therefore
  **2 of 5**, which is the number that describes real coverage. 137/137 describes
  consistency.
- **An emphatic denial got a crisis card** (#14): the backward negation scan broke
  at the auxiliary "have" and never reached "not".
- **#3 was my expectation being wrong, not the engine.** Giving away every
  possession plus "this week" is exactly the plan-plus-a-timeframe gate that
  separates "I have been thinking about this" from "this is happening", and the
  minimising "it feels like tidying up" argues for caution rather than against it.
  Recorded as `probe-003` saying so.
- **#8 was an engine bug, not a data gap.** Adding the benign frame was not enough:
  collapsing repeated letters rewrites "embarrassment" to "embarasment", which broke
  the frame in the collapsed variant while leaving "i am dying" intact, so an idiom
  the honest text had dismissed was re-admitted from a rewrite of itself.
- **#14 also filed a self-directed thought under `harm_to_others`.** The category is
  recorded, so that corrupts the assessment even when the level is right.

Root causes fixed — data unless stated otherwise: `ip.suicide_note` gained the
leave-behind idiom; `hope.no_reason_to_live` gained "waking up" behind an
existential qualifier; `ho.want_to_hurt_others` gained anyone/anybody;
`bn.jibon_sesh` allows an intervening adverb and no longer requires `jibonta`
unspaced; new `bn.jibon_shesh_korbo` (the active colloquial form, imminent) and
`bn.ar_parchi_na` (deliberately narrow — "ar *porte* parchi na" is "I cannot study
anymore", what every Bengali student says during exams); auxiliaries and "once"
added to `transparent_words`, with modals deliberately excluded;
`ho.thinking_about_hurting` excludes a first-person object and new
`sh.thinking_about_hurting_myself` answers it; and `_match` (**engine**) now judges
the primary first so its verdicts bind every derived view. 178 rules → 181.

Every widening was then probed for the benign sentence it might have started
catching, and those controls are in the table too (`probe-016`…`probe-028`).
`probe-026` is the most important control in the file: making "have" transparent
lets the negation scan see further back, so it checks that
"i have hurt myself before but i stopped last year" is still answered as a
disclosure and not mistaken for a denial.

The two new invariant tests were verified to have teeth by temporarily reverting
the `_match` guard: the figurative test fails LOW instead of NONE without it and
passes with it. A regression test that passes either way is not a test.

**After the fixes: 15/15 on the probe, 542 safety tests, 1199 backend tests, and
not one assertion weakened or deleted.**

### PASS/FAIL table (Message 2 checklist)

| check | result |
| --- | --- |
| `pytest backend/tests/safety -q` | **PASS** — 542 passed |
| case counts printed per risk level | **PASS** — 46/16/19/121/16 = 218 |
| 15 new invented phrases, mistakes flagged honestly | **PASS** — 6 of 15 disagreed on first contact; all triaged, all fixed |
| `helplines.json` valid against its JSON schema | **PASS** — 0 errors |
| `source_url` + `last_verified` on every entry | **PASS** — 34/34 |
| `needs_verification` entries listed | **PASS** — 4, each with an explanatory note |
| `curl .../crisis/resources?region=IN` | **PASS** — HTTP 200, 112 + 5 lines + 2 directories |
| `curl .../crisis/resources?region=US` | **PASS** — HTTP 200, 911 and not the DEFAULT 112 |
| no code path logs/stores raw text (grep shown) | **PASS** — no logging call, no file write, canary absent |
| frontend test: `tel:` links | **PASS** — "renders every helpline as a diallable tel: link" |
| frontend test: keyboard focus | **PASS** — "is reachable and focusable by keyboard, in order" |
| HIGH / IMMINENT templates printed for review | **PASS** — no method information in any of the 15 |
| measured recall on HIGH+IMMINENT | **PASS with caveat** — 137/137 in-table (circular); **2/5** on first contact out-of-table |
| full backend lint + test | **PASS** — ruff/mypy clean, 1199 passed 2 skipped, 98% coverage |
| full frontend lint + test + build | **PASS** — tsc/eslint/prettier clean, 89 tests, build clean |

### Needs a human, not this sandbox

1. **Native-speaker review of the hi and bn crisis copy.** Structurally tested and
   screened for method language; whether it reads as warm is not testable here.
2. **Re-verify the four flagged helplines by calling them**, and spot-check the
   rest. Data was gathered from official sources on 2026-10-09 via web search; the
   sandbox cannot place a call, and `in-kiran`'s ministry page was never reachable.
3. **Read `en/crisis.high` and `en/crisis.imminent` aloud** and decide whether you
   would want to receive them. The full text is in the verification output above.

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
`frontend` (7 steps), `secrets-scan` (2 steps), `dependency-audit` (report-only
steps). The first true lint of the workflow is its first Actions run.

First-run outcomes, recorded from GitHub after pushing (runner logs are not
fetchable from this sandbox — egress — so conclusions are quoted from the
Checks API): run 1 (initial PR head) — backend **pass** (1m17s), frontend
**pass** (33s), secrets-scan **pass** (6s), audit job **fail** (24s): the
pip-audit side was clean on the runner too, while `npm audit --omit=dev`
exited 1 on the two triaged advisories, and because `continue-on-error`
keeps the workflow gate green but still paints the job check red, the PR
showed one permanent ❌. The audit steps were rebuilt to never fail
(findings → job summary + `::warning` annotations, commit `0781883`); run 2
(Actions run 37996282865) concluded **success with all four jobs green** —
backend, frontend, secrets-scan, and the report-only audit (which still
emits its warnings and summary).

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
- [0007 — CI pipeline, Docker packaging, and containerised dev stack](docs/adr/0007-ci-and-docker.md): one workflow whose blocking checks mirror `make lint`/`make test` (80 % coverage gate as tripwire, secrets scan blocks, dependency audit report-only via never-failing steps + summary/annotations until the triaged advisory backlog clears); dev-only auto-migrations guarded in the API image's *entrypoint* (`APP_ENV=development` + `RUN_MIGRATIONS=true`), so production posture travels with the image; the API image ships without the `nlp` extra and compose runs `EMOTION_ANALYZER=keyword`; same-origin `/api` proxy in the web container (no CORS in the container path); labelled dev-only compose defaults including an all-zero-bytes Fernet key; liveness (not readiness) as the container healthcheck; the API runs as non-root `app`.
- [0008 — Crisis detection: rules engine and helplines](docs/adr/0008-crisis-detection-rules-engine.md): rules over a classifier (a level plus pattern ids is auditable in five seconds, a probability is not, and no threshold is right because too low makes every bad day a crisis card); patterns in YAML data files loaded once and validated at import, so a typo is a startup failure rather than a silent hole; **five wire levels mapped onto the four stored tiers** (`_STORED_BY_LEVEL`, MEDIUM→`elevated`, HIGH and IMMINENT→`crisis`) so the Day 8 enum and the Day 5 database enum stay independent and a new level needs no migration; escalation as a five-row policy table with no branches, so a reviewer sees every behaviour by reading five rows; deterministic pre-written copy in `content/i18n` with `{emergency_number}` the only whitelisted placeholder; helplines as one JSON file behind a schema with `source_url` + `last_verified` per entry and a `needs_verification` flag rather than an unverified number; `access_to_means` recording presence only; privacy enforced structurally (the layer that builds the reply never receives the message, and `RiskAssessment` cannot carry prose); and conservative-by-default level resolution with IMMINENT requiring evidence rather than intensity.
- [0009 — A one-way ML backstop next to the rules engine](docs/adr/0009-safety-ml-ensemble.md): the ensemble is a ratchet — `final = max(rules, ML-if-confident)`, property-tested never-lowers; three raise bands (confident top class, crisis mass `P(high)+P(imminent)`, uncertain → MEDIUM check-in) with env-configured thresholds grid-chosen on dev only; a pragmatics gate so ML may not undo the rules' negation/figurative discounts; TF-IDF + calibrated logistic regression committed as a joblib artifact with provenance, behind `SafetyClassifier` so a transformer can replace it without touching the ensemble; the 572-case eval set split train/dev/frozen-test with the test hash in `manifest.json` and no method words anywhere; and the trade that moves metadata-only `safety_events` writes onto the public assess endpoint.
- [0010 — The LLM provider layer: one interface, a chain that cannot fail](docs/adr/0010-llm-abstraction.md): `LLMProvider` with `complete` + `stream` (streaming abstract from day one, declared without `async` so an unconfigured provider fails at call time rather than at the first token); a typed error tree whose `retryable` flag — not a list of exception names — drives the retry machinery; `primary → ollama → canned` with a terminal link that cannot fail, which is what makes "a missing API key is a degradation, not a 500" true by construction; retry with **full jitter**, a hard timeout spent **across** all attempts, and a breaker with a half-open probe, all injectable so no test sleeps; redaction as an egress policy inside the chain rather than a caller's responsibility; the system prompt as a versioned, hashed, load-time-validated file; and a token guard that truncates (never rejects) at a word boundary so a redaction placeholder is never cut in half. **Numbered 0010, not the `0003-llm-abstraction.md` the brief asked for** — 0003 is the published data-layer ADR, ADR numbers are permanent, and the two precedents (0006, this one) are recorded at the top of the document.
- [0011 — The chat orchestrator: one pipeline, and the order is the safety property](docs/adr/0011-chat-orchestrator.md): nine steps in a fixed order with the crisis gate before anything that can call a model; fail **closed** on input safety (503) and fail **soft** on everything else (fallback template, `emotion=None`, `persisted=false`); ephemeral-by-default sessions (process memory, 30-min sliding TTL, no user-linked rows — MEDIUM+ turns write an *anonymous* SafetyEvent) with `store_chat` required only to save; someone else's session is a 404, never a 403; hints live in the system prompt and crisis turns are dropped from the model's window; MEDIUM gets a hint *and* the template's check-in appended; SSE `token`… `final` with `final.reply` authoritative; short-lived DB sessions because streams outlive request dependencies; per-user rate limit after validation (also applies to crisis messages); and an honest note that the Day 9 ML backstop's false positives are now user-visible.
- Smaller calls made on Day 8, recorded here because they are not obvious from the code: negated ideation scores **LOW, not NONE** (somebody telling a mental-health companion about death, even in the negative, has said something worth a check-in); `cant`/`cannot`/`unable` are deliberately **not** negation cues because inability is not absence — "I can't go on" is a crisis; figurative suppression is per *occurrence* and requires positive evidence, never the absence of risk words; a derived view of the text (leet, corrected, collapsed, squashed) may **add** a hit the honest text hid but can never **cancel** one, because otherwise obfuscating a refusal made it escalate; third-person framing caps IMMINENT→HIGH but never for `acute_medical`; the supporter template requires third person *and* (fiction, quotation, or no speaker), so "my husband threatens to kill me" correctly gets the self-facing card; `resources_for(region)` intentionally mixes a region's own entries with the DEFAULT directories while dropping the DEFAULT emergency entry when the region has one; and `load_patterns`' `lru_cache` is permitted because its parameters are all keyword-only — a test asserts no cache in the package could be keyed on somebody's message.
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
- **The CI workflow's first real validation was its first run** (PR #8):
  backend, frontend and secrets-scan passed; pip-audit was clean on the runner
  too; `npm audit --omit=dev` exited 1 on the two known advisories, and —
  because `continue-on-error` still renders the job check red — the PR showed
  a permanent ❌. The audit steps were rebuilt to never fail (findings go to
  the job summary + warning annotations); the replayed run went all-green.
  Details in "Verification (Day 7)".
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

### Day 8 safety notes

- **Nothing routes through the gate yet.** Only `api/deps.py` and `api/v1/crisis.py`
  import `app.services.safety`; `api/v1/chat.py` still carries its placeholder
  ("Message send/receive, the crisis gate and the output-safety check arrive with…").
  AGENTS.md rule 1 — detection before any LLM call — is *satisfied structurally*
  and verified against the crisis endpoints, but it becomes real only when Day 9's
  message-send path calls the engine first. Do not describe Manovia as having
  crisis detection in production until that wiring exists.
- **Detection coverage is bounded by what somebody thought to write down.** The
  out-of-table probe is the evidence: three of five invented high-risk phrases
  returned NONE after 190 curated cases were green. There is no statistical
  backstop for a phrasing nobody anticipated, and no labelled corpus of real
  messages (which this repo should not collect). Mitigation is a cadence, not a
  test suite — see `docs/safety-design.md` §10.
- **The Hindi and Bengali crisis copy has had no native-speaker review.** It is
  structurally tested and screened for method language, not tested for whether it
  sounds warm to the person who will read it. Highest-value outstanding review.
- **Helpline data is a snapshot and will go stale.** Researched 2026-10-09; four of
  34 entries are flagged `needs_verification` (`in-icall` conflicting published
  hours, `in-kiran` no reachable ministry page, `in-aasra` third-party listings
  disagree, `au-kids-helpline` official site not fetched). Five more carry a
  `verification_note` explaining an indirect source without being flagged. Nobody
  has placed a test call — the sandbox cannot. Numbers change; this file needs an
  owner and a re-check date, not just a `last_verified` field.
- **No tense reasoning.** "I used to self harm at school but I stopped two years
  ago" gets the crisis card. Inferring past tense reliably is hard and getting it
  wrong the other way — reading "I cut myself, then I stopped the bleeding" as
  history — is worse. Accepted cost; the false positive is cheap.
- **Broad risk words stay broad, and it produces visible false positives.**
  "i have a plan for the weekend barbecue" scores HIGH via `ip.have_a_plan`. A
  hypothetical question about dying scores HIGH. "My husband threatens to kill me"
  is recorded under both `abuse_disclosure` and `suicidal_ideation`. Deliberate:
  narrowing `si.kill_myself` to first-person subjects would risk missing
  "I am going to kill me", which people do write. The reply is identical either way.
- **English is over-represented**: 136 English rules against 24 Hindi and 21
  Bengali, for a product whose primary audience is Indian. Two of the six probe
  defects were Bengali, from a set one sixth the size — roughly what that ratio
  predicts.
- **`POST /api/v1/crisis/assess` writes no `safety_events` row.** Deliberate: it is
  public and unauthenticated, so anyone could fill the table. `policy.record_event`
  tells the authenticated chat path when to write one; that path does not exist yet.
- **`patterns.py` is at 83 % coverage** — the loader's rejection paths (unknown key,
  uppercase value, non-compiling regex) are partly untested, and those are exactly
  the guards that turn a data typo into a startup failure instead of a silent hole.
- **No detection eval harness.** `evals/` is still empty directories; the same debt
  as the Day 6 emotion model. The 218-case table is a regression suite, not an
  accuracy measurement on real traffic.
- Detection is synchronous and runs 181 rules across four variants plus two
  projections per message. Measured at 0.34 ms/message with input capped at 4000
  characters, so the cap is what bounds it; there is no per-rule deadline and a
  pathologically written regex would be a latency problem rather than a crash.

### Day 9 safety notes

- **The eval ground truth is one annotator's judgement on synthetic text.** I
  wrote every label; borderline families (farewell behaviour, "everything is
  arranged") are labelled as a careful human would, which means the eval measures
  agreement with one human, not with the world. A second annotator pass on the
  ~60 ambiguous cases is the cheapest way to make these numbers mean more.
- **The shipped operating point over-escalates benign text, deliberately.** At
  the recall-first thresholds, 27/51 benign test cases get a crisis card and 11
  more a check-in (dev: 31 + 7). A char n-gram model on 340 training examples
  cannot separate benign from crisis masses (medians 0.34 vs 0.54, badly
  overlapping). The precision-leaning alternative (`crisis_mass_floor` 0.45–0.50:
  recall ~0.80–0.84, crisis-card false alarms cut ~2/3) is documented in
  `threshold_tuning.md`; switching is a config change, no retraining.
- **Rules-only false positives are unchanged and are part of the same bill.**
  Figurative "die laughing" → HIGH, past-tense recovery stories → HIGH (no tense
  reasoning), awareness discussions → HIGH. The eval confusion matrix reports
  them against ground truth by design.
- **The classifier is a 572-case baseline.** Dev accuracy 0.543 / macro F1 0.459
  — it earns its place as a recall backstop, not as a standalone detector.
  Indic training data is thinner than English (82 hi / 107 hi-Latn / 88 bn vs
  218 en), and the sandbox has no Hugging Face Hub access, so no transformer or
  NLI experiment was even attempted; the `SafetyClassifier` interface is the hedge.
- **Per-level calibration at this size is noisy** (sigmoid, cv=3 on ~340
  examples). The masses order evidence well enough to threshold on; they are not
  frequencies.
- **`safety_events` rows from the assess endpoint are anonymous** (NULL
  user/session) and the endpoint is public: an attacker under the rate limit can
  add metadata rows. Accepted for the audit trail; the authenticated chat gate
  attaches identities when it lands.
- **MEDIUM/LOW separation is weak** (test MEDIUM recall 0.000, LOW 0.077): the
  ensemble mostly promotes them to HIGH or holds them at NONE. That is tolerable
  today — the product policies that differ between them are tone and resources,
  and raising is safe — but a future tune should look at the bands between
  suspicion floor and crisis-mass floor.

### Day 10 LLM notes

- **The system prompt has never been read by a model.** Every check on it is a
  regex over a file. It states the nine rules, it does not prove the model will
  follow them, and 402 words of instruction is a lot to compete with everything
  else a model knows about being helpful. The first real conversation is the
  first real test.
- **Nothing here has been tried against a real provider.** No `ANTHROPIC_API_KEY`
  in the sandbox, no Ollama installed: the request shape, the response parsing
  (`content` blocks, `usage`, NDJSON) and the error translation are all verified
  against stubs. The stubs match the documented SDK shape, and the real SDK
  error classes translate correctly when the extra is installed (one test checks
  that, and skips when it is not) — but "the stub agreed with us" is not "the API
  agreed with us".
- **The chain caught only `LLMError` at first**, so a provider raising anything
  else became a 500. Found by running `scripts/llm_probe.py`, fixed, and pinned
  by six tests. Recorded here because it is the kind of bug that only appears when
  somebody actually runs the code rather than reading it.
- **Redaction over-matches, and the cost is reply quality.** Every URL becomes
  `[URL]` even when it is a link to our own privacy policy, and a date written
  `2024-01-15` matches the grouped-phone pattern and becomes `[PHONE]`. The
  direction is right — losing a detail is free, leaking one is not — but it means
  the model sees a garbled sentence sometimes, and nobody has measured whether
  that makes replies worse.
- **Names are not redacted.** `LLM_REDACT_NAMES` is `false` by default and the
  `NullNameFinder` finds nothing, so a person who signs their message or names
  their employer sends it. For an Indian user base, where the available NER
  models are English-centric and many given names are also ordinary words, this
  is a real gap rather than a tick-box.
- **The token estimate is wrong for Devanagari and Bengali.** 4 characters per
  token under-counts multi-byte UTF-8, so the window keeps *more* Indic turns
  than the budget intends. It errs toward spending more rather than cutting
  somebody off, but the number in `GuardReport.input_tokens` is not the number on
  the invoice.
- **The provider's own retry loop is disabled** (`max_retries=0`) so ours owns
  the deadline and the jitter. If a future SDK does something smarter there, we
  have opted out of it.
- **The reversible redaction map is dropped, not stored.** That is the right
  default, but it means a future feature that wants to show the user their own
  message with names intact has no way to do it and will be tempted to start
  persisting the map. Do not: the moment it is persisted, redaction is decorative.
- **Streaming is built and tested end to end but not exercised by any caller.**
  No endpoint streams yet, so the mid-stream-failure path (raise, do not restart)
  has only ever run in a test.

### Day 11 chat notes

- **The ML backstop turns ordinary messages into crisis responses (HIGH).** The
  committed `crisis_v1` artifact (340 training cases, dev accuracy 0.54, macro-F1
  0.46) with `SAFETY_ML_CRISIS_MASS_FLOOR=0.30` raises to HIGH on a crisis mass
  that benign text routinely reaches (medians 0.34 benign vs 0.54 crisis — Day 9's
  own note that the two overlap). On `/crisis/assess` this was "the false positive
  is cheap"; on every chat turn it is a person asking for a study plan and being
  handed a helpline. **Not fixed on Day 11** — choosing a threshold is a safety
  policy call, not a plumbing one. Options for the owner: (a) raise the floor to
  0.45–0.50 (Day 9 predicted the recall cost — measure it on the dev split, not by
  hand), (b) let the ML backstop raise only to MEDIUM in chat and keep HIGH for
  rules, (c) retrain on a much larger, more varied set, (d) ship rules-only
  (`SAFETY_ML_ENABLED=false`) until one of those. Whichever is chosen, re-run
  `make eval` and keep the test that HIGH never reaches the model.
- **`GET …/stream?message=` puts the text in the URL.** Proxies and CDN logs
  record URLs. The app's own access log is path-only. Prefer the POST stream; the
  GET form exists for `curl` and `EventSource`-style clients (which cannot set an
  `Authorization` header anyway, so the future UI will `fetch()` the POST).
- **Ephemeral store and chat rate limiter are per-process.** Fine for one uvicorn
  worker; with several, an ephemeral session only exists on the worker that made
  it and the rate limit is per worker. Needs a shared store before scaling out.
- **Concurrent messages in one session are not serialised**; two simultaneous sends
  can interleave in history.
- **The rate limit also applies to crisis messages** (30/min/user by default).
  High enough that a person in distress will not reach it; the public `/crisis/*`
  endpoints are unaffected.
- **No end/delete-session endpoint.** `409 session_ended` exists for rows ended
  elsewhere; there is no way yet for a user to end or delete a saved chat.
- **A client disconnect mid-stream does not persist the partial reply.** The user
  message and its SafetyEvent are already committed; the assistant half is lost.
- **A lone surrogate in a JSON body is a generic `validation_error` 422**, not
  `message_encoding`: the JSON parser refuses it before our validation sees it.
  The curated check still covers every other entry point (unit-tested).
- **Retrieval and the output guard are stubs** (`[]`, pass-through) until Days 13
  and 14. AGENTS.md rule 4 ("every LLM output passes an output-safety check") is
  satisfied by the *seam*, not yet by a real check — the stub is the weakest link
  until Day 14.
- **The emotion analyser logs a WARNING per message when the `nlp` extra is not
  installed** (`emotion_analyzer_failed`, Day 6 chain behaviour). Harmless but
  noisy in this sandbox.
- **`frontend/README.md` fails `prettier --check`** (pre-existing; not in CI; not
  touched).

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
- **(Day 7)** Flip `dependency-audit` back to blocking (drop the `exit 0` guards so native exit codes flow) once the triaged advisory backlog is cleared; review the two react-router advisories when react-router 7 is adopted.
- **(Day 7)** Unprivileged nginx image or a `nginxinc/nginx-unprivileged` base for the web container; route-based code-splitting when the bundle justifies it.
- **(Day 7)** Production migration runbook (one-shot `alembic upgrade head` container) and a `/ready` migration-level check once deploys exist.
- **(Day 8)** A detection eval harness in `evals/` — a held-out labelled set, precision/recall per category and per language, and a report artifact — so "is detection getting better?" stops being answerable only by re-running the probe by hand. Blocked on a source of real-ish messages that does not mean storing anybody's.
- **(Day 8)** A scheduled out-of-table probe: generate or collect phrases from outside `cases.yaml`, run them, and file every disagreement as an issue. Today this is a manual discipline described in `docs/safety-design.md` §10.
- **(Day 8)** A helpline re-verification job: alert when `last_verified` is older than N days, and a checked-off record of somebody actually calling each number. Consider sourcing from an API (Find A Helpline) rather than a hand-maintained JSON file.
- **(Day 8)** More Indic languages, and native-script coverage beyond Hindi and Bengali — Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Urdu. Each needs a patterns file with `language:` set, a locale file with all five template ids, and cases written by somebody who speaks it.
- **(Day 8)** Community code words and slang change faster than a YAML file gets reviewed; detection needs a named owner and a review cadence. Consider a lightweight intake path for clinicians or moderators to propose phrases.
- **(Day 8)** Tense and aspect reasoning, so a disclosed *history* of self-harm ("I used to… I stopped two years ago") can be distinguished from a current one without losing "I cut myself, then I stopped the bleeding". Hard, and the current false positive is cheap — do not attempt it casually.
- **(Day 8)** A second negation strategy for long or unpunctuated messages: the backward scan is bounded at 8 tokens and 5 transparent words, so a rambling message can put a cue out of reach. Clause splitting helps but does not cover text with no punctuation at all.
- **(Day 8)** Per-rule telemetry — which patterns fire, how often, and how often a fired pattern is later suppressed — so broadening decisions are made from data rather than from reading regexes. Must stay metadata-only.
- **(Day 8)** A `needs_verification` review UI or checklist for maintainers, so flagged entries are worked down rather than shipped indefinitely with a caveat.
- **(Day 8)** Region detection: today `region` is a caller-supplied query/body parameter. Deriving it safely (locale, timezone, explicit user choice) without collecting location data is an open design question.
- **(Day 9)** A second annotator pass over the ~60 ambiguous eval cases, and a
  small inter-annotator-agreement note; then consider relabelling the families
  where the disagreement clusters (farewell behaviour, "everything is arranged").
- **(Day 9)** A fine-tuned multilingual transformer behind `SafetyClassifier`
  once Hub access and more labelled data exist; re-run the frozen test split
  before touching thresholds. Same interface, same ratchet.
- **(Day 9)** Grow the Indic training families (bn/hi/hi-Latn are ~48% of cases
  but carry the hardest phrasings); code-mixed Hinglish cases are absent.
- **(Day 9)** A precision-leaning operating-point switch for products that decide
  alarm fatigue outweighs the last few points of recall (documented curve in
  `evals/reports/threshold_tuning.md`).
- **(Day 9)** Live traffic triage: the metadata-only `safety_events` rows plus
  the assess endpoint's fingerprints are enough to spot escalation-rate anomalies
  without ever storing text; no such dashboard exists yet.
- **(Day 9)** The eval runner's per-level MEDIUM/LOW tuning (see Day 9 safety
  notes) and an explicit "check-in false alarm" budget next to the recall target.
- **(Day 10)** An output-safety check on every completion (AGENTS.md rule 4),
  reusing the Day 8 safe-messaging vocabulary rather than growing a second list —
  the half of the Day 10 brief that is still missing.
- **(Day 10)** A prompt eval set: ~40 hard messages (self-harm disclosure,
  medication question, "are you a real therapist?", Hinglish, very long, very
  short) scored against the nine prompt rules, run offline against the Fake for
  plumbing and by hand against a real model. Today the prompt is regex-checked,
  not behaviour-checked.
- **(Day 10)** A real NER name redactor behind `NameFinder` (Indic languages
  first — an English-centric model would miss most of this product's names), so
  `LLM_REDACT_NAMES=true` becomes a setting worth turning on.
- **(Day 10)** Token counting that matches the provider: use Anthropic's
  `/v1/messages/count_tokens` (or a real tokenizer) instead of `chars / 4`, which
  under-counts Devanagari and Bengali badly enough to matter for the window.
- **(Day 10)** Streaming wired to a real endpoint, so the mid-stream-failure path
  is exercised by traffic and not only by tests.
- **(Day 10)** Per-provider telemetry: attempts, breaker state, degraded rate and
  `fallbacks_used` exported next to the existing `describe()` output, so "are we
  running on the canned reply and nobody noticed?" is answerable from a
  dashboard.
- **(Day 10)** A cost dashboard / per-request ceiling enforced across a
  conversation rather than per turn, once there is traffic to meter.
- **(Day 10)** Prompt A/B infrastructure: two versions live behind a setting, with
  the version and its sha256 recorded on every stored message so a reply can
  always be traced to the prompt that produced it.

- **(Day 11)** Wire `ChatPage.tsx` to the stream: `fetch()` POST + `ReadableStream` (not `EventSource`), render tokens, swap in `final.reply` when `replaced`, `CrisisCard` from `metadata.crisis`, an honest "degraded" notice.
- **(Day 11)** `DELETE /chat/sessions/{id}` (end/erase), history pagination, and a "delete all my chats" path that ties into the Day 17 erasure work.
- **(Day 11)** Serialise turns per session (an asyncio lock per session id, or an advisory DB lock) so concurrent sends cannot interleave.
- **(Day 11)** A shared ephemeral store and rate limiter (Redis) for multi-worker deployments, behind the existing `EphemeralSessionStore` and limiter interfaces.
- **(Day 11)** Persist the partial reply on disconnect, flagged as incomplete, if product wants it; today it is dropped.
- **(Day 11)** A `/metrics`-style latency histogram per pipeline step (the `chat_turn` log has only the total).
- **(Day 11)** Per-turn "why" logging for the gate (rationale codes only) so threshold tuning can use real traffic without text.

## Next steps (Day 12 — first three)

**Preamble (needs a human, before any user sees the chat):** decide what to do
about the ML backstop's false positives (Known issues → Day 11 chat notes). Until
then, run with `SAFETY_ML_ENABLED=false` for anything demo-facing.

**Preamble (carried, needs my machine):** the Docker commands from "Verification
(Day 7)"; `scripts/llm_probe.py` with a real key; the native-speaker review of the
hi/bn crisis copy and the four `needs_verification` helplines.

1. **Settle the chat-time safety policy and re-measure.** Pick one of the options in
   the known issue, change it in config/`ensemble` only, re-run `make eval`
   and a benign-message set (add one to `evals/` — eleven ordinary messages
   showed 7 HIGH), and add a regression test that a short list of ordinary
   messages is not HIGH under the shipped defaults while "I want to kill myself"
   still is.
2. **Chat page on the stream** (parking lot, first item): POST-stream via `fetch`,
   token rendering, `CrisisCard` from `metadata.crisis`, helplines one tap away,
   and the `degraded` notice. This is the first time the SSE contract meets a
   browser, so check it through the Vite proxy too (buffering).
3. **Retrieval (Day 13) behind the existing `Retriever` protocol**, or — if the
   plan puts the output guard first — the output-safety check behind
   `OutputGuard` (AGENTS.md rule 4 is only met by a seam today). Either way the
   orchestrator's order and wire format must not change; the stub tests are the
   contract.
