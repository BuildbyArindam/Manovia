# Manovia — Progress

## Current status

Day 6 NLP service is implemented and offline/live fallback verification is complete:
**356 backend tests pass at 100% app coverage**, **73 frontend tests pass**,
backend/frontend lint, type checks, frontend formatting and build all pass.
The optional real-model test is **skipped**, not passed: Hugging Face config
fetch fails with TLS EOF/SSLError and torch is not installed. Real-model
accuracy, licence confirmation and CPU latency remain local checks.

Work is on platform-fixed `arena/5c0eaea5-manovia`, based on the supplied
Day 6 starting commit (not recreated from main). All milestones are committed
and pushed only to that branch. A single Day 6 PR will target main; do not merge
without user review. Prior Day 5 status below is historical.

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

- Day 6: [ADR 0002 — emotion model](docs/adr/0002-emotion-model.md), optional
  English HF model, offline default/fake, bounded degradation and hash-only cache.

- [0001 — Monorepo and stack](docs/adr/0001-monorepo-and-stack.md): FastAPI + Pydantic v2, SQLAlchemy 2 + Alembic + PostgreSQL, React + Vite + TypeScript + Tailwind, provider-abstracted LLM integrations with offline fakes, Docker for local orchestration.
- [0002 — Backend skeleton: configuration, logging, and error conventions](docs/adr/0002-backend-skeleton-conventions.md): error envelope, structlog JSON logging with request IDs and field redaction, env-only settings, security headers, offline ASGI-transport testing.
- [0003 — Data layer: models, migrations, encryption at rest, and repositories](docs/adr/0003-data-layer-models-migrations-and-encryption.md): typed models + naming convention, portable types (no native enums), UTC timestamps with dual defaults, erasure-as-one-`DELETE` via cascade (+ the SQLite pragma), encryption at the repository boundary with one deployment key today and per-user keys later, metadata-in-the-clear/words-encrypted split, async sessions with thin never-commit repositories, hand-reviewed migrations with drift as a test failure, readiness = connectivity (not migration state).
- [0004 — Authentication, anonymous mode, and consent](docs/adr/0004-authentication-and-consent.md): argon2id + length-only password policy, fixed-HS256 access/refresh JWTs with hashed storage and rotation-family reuse detection, consent documents as versioned content enforced at the *current* version by `require_consent`, in-house sliding-window rate limiting + per-account lockout with backoff, `ApiError` curated codes in the Day 2 envelope.
- [0005 — Frontend skeleton: tokens, the API client, and the accessibility floor](docs/adr/0005-frontend-skeleton.md): CSS-variable design tokens with a `data-theme` switch (no `dark:` classes, no literal colours in components), contrast enforced by a test on the tokens, one API client with single-flight token refresh and one replay, onboarding as a gate rather than a guarded route, one modal primitive owning the focus contract, one navigation rendered as rail or bottom bar by a media query, and a deliberately public crisis endpoint.
- Smaller calls made on Day 4, recorded here because they are not obvious from the code: login and upgrade return the same `invalid_credentials`/`email_taken` shapes whether or not the account exists (login is constant-time; registration cannot hide that an address is taken); logout is possession-based and idempotent so it never becomes an account oracle; `upgrade` revokes every refresh family because an identity change should sign everything out; a consent version bump closes gated features until re-consent (intended); `alembic/versions/0002` was autogenerated and hand-reviewed in the 0001 style (named constraints, explicit downgrade); models gained `as_utc()` because SQLite hands back naive datetimes and `expires_at` comparisons must not mix naive/aware.

## Known issues

### Day 6

- HF weights/model card cannot be downloaded here (actual config request failed
  with SSLError/TLS EOF). Optional torch runtime is not installed; no real-model
  inference, accuracy or latency claims. See [ADR 0002](docs/adr/0002-emotion-model.md).
- Keyword results are sensible for the requested samples, not a multilingual
  accuracy evaluation: sarcasm, complex negation, romanisation variants and
  context can be missed. GoEmotions has no loneliness label; HF cannot infer
  loneliness explicitly. Valence/arousal are heuristic projections, not clinical
  measurements. English HF inputs only; hi/bn/Hinglish use fallback.
- Deadline bounds HF waiting, not native cancellation. A hung worker remains
  bounded to one in-flight input and forces fallback until restart. Cold loading
  may exceed the default deadline. Degraded cache entries last until eviction.
- Legacy `chatbot-1` absent (local search and GitHub legacy path 404). Sentiment
  is a standalone polarity approximation, not a claimed faithful port.
- `npm ci` reports 9 dependency advisories (4 moderate, 5 high); no dependency
  upgrades made outside today's scope. Existing frontend act/router warnings
  still appear during passing tests.
- ADR numbering 0002 already exists for backend skeleton conventions; kept the
  user-requested `0002-emotion-model.md` filename without renumbering history.


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

- NLP-specific future work: evaluated multilingual emotion model, held-out
  language/negation/sarcasm accuracy fixtures, calibrated dimensional scores;
  cancelable model process isolation, cache expiry/provider provenance and
  model revision pinning after local validation. Not added to the Day 6 API.


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

## Next steps (Day 7 — first three)

1. Confirm Day 7 scope; build deterministic crisis/self-harm rules as a pure
   offline module with fixtures **before any LLM call**. Do not use emotion
   analysis as a crisis gate or a diagnostic label.
2. Define the LLM provider interface with offline Fake and mandatory output
   safety checking; keep raw identifiers and text out of logs.
3. Build consent-gated message sending on the existing encrypted repositories,
   recording metadata-only safety events; wire frontend chat only after gates
   are tested. These are proposed next steps, not implemented in Day 6.

## Day 6 implementation milestone (2026-10-09)

- Added the abstract/result contract, deterministic Fake, keyword and sentiment
  fallback, optional lazy/serialized CPU Hugging Face batch adapter, language
  detection with Hinglish routing, bounded worker/deadline and hash-only LRU.
- Added the non-production-only diagnostic endpoint and configuration examples;
  model/interpretation/privacy choices in [ADR 0002](docs/adr/0002-emotion-model.md).
- Initial verification: `HF_HUB_OFFLINE=1 make test`: 353 backend tests pass
  (1 model deselected), 73 frontend tests pass; `make lint` passes including mypy.
- Regression fixes: Unicode combining marks in keyword tokens, short English
  detection, and restoring logging after captured privacy tests. Tests retained.
- Session branch is `arena/5c0eaea5-manovia` (platform-fixed), not the requested
  `day-06-nlp-service-emotion-sentiment`; no work or pushes to main.
- Legacy sentiment source absent locally and GitHub legacy path returned 404;
  no claim of a verbatim legacy port. Real-model and live verification follow.

## Day 6 verification milestone

- Final code run: `HF_HUB_OFFLINE=1 make test` → **356 backend tests passed**,
  1 model deselected, **100% app coverage (1705 statements)**; **73 frontend
  tests passed** in 12 files. `make lint` passes ruff check/format, strict mypy
  (93 source files), and frontend ESLint. Verification script also passes ruff
  and strict mypy. Frontend typecheck, Prettier check and production build pass
  (104 modules, 236.79 kB JS / 75.28 kB gzip).
- Added CPU-runtime preflight so missing torch cannot initiate hub lookups;
  regression verifies only the runtime import occurs before graceful fallback.
  Optional model test now checks fallback even when optional extras are missing.
- Hugging Face config download was attempted for real: **SSLError**, TLS EOF
  after retries. Transformers installed; torch absent. `pytest -m model -q`
  → **1 skipped, 356 deselected**, explicitly confirming HF failure-to-fallback.
  This is not a real-model accuracy or performance pass.
- Live uvicorn APIs bound to 0.0.0.0: fallback :8000, failing HF :8001,
  production :8002 (throwaway secrets generated locally; no committed secrets).
  Six requested samples all return HTTP 200 on both dev paths:
  job → joy / +0.900; alone → loneliness / -0.700;
  Hinglish exam → fear / -0.800; fine, empty and 10k → neutral / 0.000.
  10,001 characters → 422. Production diagnostic POST → 404; tests also
  verify the route is absent from production OpenAPI.
- Final 20 sequential warmed, **uncached HTTP** calls (cache size 0,
  explicit en, p50 median / p95 nearest-rank): fallback **1.683 / 1.994 ms**;
  failed HF → fallback **1.715 / 2.249 ms**. No real-model timings available.
- Separate cache-enabled run: two sentinel POSTs → 200; second logs only
  `emotion_cache_hit` + SHA-256 hash. Grep of all four server logs found zero
  raw sample/sentinel matches. Provider-error privacy tested with exceptions
  containing raw text; redaction also removes nested `text` fields.
- Reproduction script: `scripts/verify_day6_nlp.py`; use `--expect-fallback`
  for exact lexicon assertions, omit for real-model accuracy observations.
- Prettier initially failed on existing frontend README table/emphasis style;
  formatting-only correction made, then full format check/build rerun clean.

## Day 6 wrap-up decisions and local checklist

- Completed work and verification are recorded above; [ADR 0002](docs/adr/0002-emotion-model.md)
  covers model choice/licence caveat, max-score taxonomy mapping, English-only
  HF routing, fixed projections, bounded degradation and hash-only cache.
- Manually check on a machine with Hugging Face access:
  1. Install `pip install -e 'backend[nlp,dev]'`; reconfirm model card/licence.
     Run `cd backend && pytest -m model -q` and retain actual results.
  2. Start with `EMOTION_PROVIDER=hf`, `EMOTION_MODEL_ID` from `.env.example`,
     `EMOTION_CACHE_SIZE=0`, `EMOTION_TIMEOUT_SECONDS=60` for cold load. Run the
     verification script (omit `--expect-fallback`); inspect English accuracy
     and collect real CPU model p50/p95 separately from degraded/cached calls.
  3. Review Hindi/Bengali/Hinglish phrasing, negation and sarcasm; supply missing
     legacy source for comparison. No clinical or crisis accuracy claims.
- No secrets, model weights, logs, local databases, build output or dependency
  directories are committed. No merge is authorized or performed.
