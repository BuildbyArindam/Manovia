# 0004 — Authentication, anonymous mode, and consent

Date: 2026-10-09
Status: Accepted

## Context

Day 4's scope is accounts and consent: guest (anonymous) mode, email/password
registration, session tokens with rotation, consent recording and enforcement,
rate limiting, and lockout after repeated failed logins. The product rules that
shape this design: privacy-first (anonymous use must work), safety-first (the
AI disclosure must be shown and accepted before AI-facing features), and the
AGENTS.md constraints (credentials never logged, no secrets in code, every
external dependency behind an interface with offline tests).

## Decisions

### 1. Argon2id passwords, length-only policy

`argon2-cffi` behind a `PasswordHasher` protocol (`app/core/passwords.py`).
The policy is **min 10 characters, max 256, no composition rules** — NIST
SP 800-63B: composition theatre pushes users to predictable substitutions and
does not stop credential stuffing. Login failures are constant-time and
constant-message (`invalid_credentials`), with a dummy argon2 verification for
unknown accounts so timing does not leak account existence.

### 2. Access/refresh JWTs, fixed HS256, two-part refresh tokens

Access tokens (15 min) carry only `sub`/`type`/timing claims; refresh tokens
(7 days) add `jti` and `fam` (rotation family). The algorithm is fixed to
HS256 and deliberately not configurable (algorithm-confusion surface). A
refresh token is only usable alongside its hashed row in `refresh_tokens` —
the database alone can never mint a session. Secrets come from `SECRET_KEY`
(production-required since Day 2); without one locally, an ephemeral key is
generated at startup with a warning (sessions die on restart — acceptable for
development).

### 3. Rotation families with reuse detection

Every sign-in starts a family; `POST /auth/refresh` rotates inside it and the
old row gets `revoked_at`. Presenting a revoked token is proof of theft or
replay, so the **whole family is revoked** and the client must sign in again
(`401 refresh_token_reused`). `POST /auth/logout` revokes the family of the
presented token (possession-based, idempotent, no oracle). An account-upgrade
revokes *all* families — an identity change signs everything out.

### 4. Consent documents are versioned content, checked at the current version

`app/content/consent_documents.json` is the single source of truth for the
`terms` / `privacy` / `ai_disclosure` / `store_chat` versions and the AI
disclosure text (AGENTS.md rule 3: discloses AI nature, not therapy, not a
crisis service). `POST /api/v1/consent` records grants append-only (a
withdrawal is a new `granted=False` row; newest row wins). `require_consent(*
kinds)` enforces grants **at the current version**, so bumping a document
version invalidates old grants everywhere the gate is used. The gate is wired
on `POST /api/v1/chat/sessions` (the first chat endpoint) and will be applied
to journal/mood endpoints as they land.

### 5. In-house sliding-window rate limiting and per-account lockout

No slowapi: a ~40-line `InMemoryRateLimiter` (sliding window per client IP,
strict on `/api/v1/auth/*`, moderate globally) keeps the dependency tree small
and the behaviour fully testable offline. Lockout is per attempted account
(normalised email): after 5 failures the account locks for 15 minutes, doubling
per further failure up to 1 hour, cleared by a successful sign-in. Both knobs
are environment-configurable (`RATE_LIMIT_*`, `LOGIN_*`). State is per process
— a shared store (Redis) goes behind the same interfaces when we run more than
one worker.

### 6. `ApiError` carries curated codes through the error envelope

The Day 2 handler mapped HTTP statuses to generic codes; auth and consent need
machine-readable codes (`invalid_credentials`, `token_expired`,
`refresh_token_reused`, `consent_required`, `account_locked`, `rate_limited`…).
`ApiError(status, code, message, headers)` plus a handler keeps the envelope
shape (`{"error": {"code", "message", "request_id"}}`) unchanged.

## Consequences

- Bumping a consent document version silently closes gated features until users
  re-consent — intended, and visible in `GET /consent/requirements`.
- A stolen access token is valid for up to 15 minutes (no revocation list); the
  refresh layer detects theft on the next rotation.
- Account lockout enables a targeted annoyance-lock of a victim's account
  (5 failed attempts); backoff caps at one hour and email-based unlock/notify
  is a later milestone.
- The seed script now hashes its demo password with the real argon2 hasher, so
  `demo@manovia.local` can actually sign in.

## Alternatives considered

- **slowapi / limits** for rate limiting: rejected for now — the in-house
  window is smaller than the integration, and both share the per-process
  state problem that Redis must solve anyway.
- **Opaque random refresh tokens in a store-only design** (no JWT for refresh):
  equally fine; JWTs were kept so both token kinds share one verification path
  and the `jti`/`fam` claims make the rotation bookkeeping explicit.
- **Cookie sessions with CSRF tokens**: deferred — the API is consumed with
  `Authorization: Bearer` headers, which sidesteps CSRF for now; revisit when
  the web frontend chooses its storage strategy.
