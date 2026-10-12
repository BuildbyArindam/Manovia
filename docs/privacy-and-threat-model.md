# Privacy & Threat Model

This document states, in plain terms, what Manovia stores, how it is protected,
what never leaves the user's device or the database, and — just as importantly —
what our encryption **does and does not protect against**. It is a living
document updated as new surfaces land (Day 3 data layer, Day 4 auth & consent,
Day 6 NLP redaction, Day 8–9 crisis engine, Day 11–14 chat & output guard,
Day 15 mood check-ins, Day 17 encrypted journal & envelope encryption,
Day 22 privacy controls, strict CSP and the request guards).

---

## 1. Core privacy commitments

1. **No raw personal text in plaintext columns.**
   Chat messages (`messages.content_encrypted`), conversation memory summaries
   (`chat_sessions.memory_encrypted`), mood notes (`mood_entries.note_encrypted`),
   and journal titles and bodies (`journal_entries.title_encrypted`,
   `journal_entries.body_encrypted`) live exclusively in `LargeBinary` columns
   encrypted with AES-256-GCM before touching SQLite or PostgreSQL.
2. **No raw personal text in logs or telemetry (`AGENTS.md` safety rule 5).**
   `app/core/logging.py` strips a hard blocklist of sensitive keys (`title`,
   `body`, `note`, `content`, `message_text`, `text`, `prompt`, `query`,
   `password`, `token`, `field_encryption_key`, `master_key`, `data_key`,
   `dek`, `wrapped_dek`, `data_key_encrypted`) before any log event is
   rendered. Journal routes log only `entry_id`, `user_id`, `template`,
   `store_emotions`, `word_count`, and `risk_level`.
3. **No raw personal text in the safety audit trail.**
   `safety_events` has no `TEXT` or `VARCHAR` column for user words — only
   `user_id`, `session_id`, `risk_level`, `source`, `category`, and
   `created_at`. Schema unit tests (`tests/unit/test_models.py`) fail CI if a
   free-text column is ever added to `safety_events`.
4. **Explicit, versioned, revocable consent.**
   Every personal surface checks `require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY)`
   against the current version in `backend/app/content/consent_documents.json`.
   Chat additionally requires `AI_DISCLOSURE`, and persisting chat turns to the
   database requires `STORE_CHAT` (without it, chat sessions live only in
   bounded in-memory ephemeral storage).
5. **Single-query hard erasure.**
   Every child table references `users.id` with `ON DELETE CASCADE`
   (`PRAGMA foreign_keys=ON` on SQLite and native FK cascade on PostgreSQL).
   Deleting the `users` row destroys `users.data_key_encrypted` and every
   dependent row in one transaction. Day 22 exposes this as
   `DELETE /api/v1/privacy/account` (re-auth + confirmation required) and
   returns a receipt that records *that* it happened, never *what* was in it.
6. **The owner can leave with their data.**
   `GET /api/v1/privacy/export` returns every table the app writes as a ZIP (or
   a single JSON document), decrypted, with a manifest that declares row
   counts, any per-table truncation and any row that could not be decrypted.
   A row that fails is reported as `null` + `decryption_failed` — never
   silently omitted. See [ADR 0022](adr/0022-privacy-controls-and-data-export.md).
7. **Retention is per user and actually deletes.**
   `privacy_settings.retention_days` (7–3650) feeds
   `services/privacy/retention.py`, run by an operator via
   `scripts/apply_retention.py` / `make retention`. Only users who set the
   setting are considered; the cutoff is exclusive; the job is idempotent and
   has a dry run. It is **not** self-scheduling — see §7.3.
8. **Nothing user-written is rendered as markup, and no inline script runs.**
   Chat bubbles, journal previews and mood notes render as text nodes; there is
   no `dangerouslySetInnerHTML` anywhere in `frontend/src`. The production CSP
   is `script-src 'self'` with no inline script in `index.html`, so an injected
   `<script>` has nothing to hide behind. Verified in a real browser against the
   built bundle, not only in jsdom (`frontend/scripts/verify-prod-csp.mjs`).

---

## 2. What is stored and how (table by table)

| Table | Encrypted columns (`LargeBinary`, AES-256-GCM) | Cleartext metadata columns | Why cleartext metadata exists |
| --- | --- | --- | --- |
| `users` | `data_key_encrypted` (wrapped 256-bit per-user DEK) | `id`, `email`, `password_hash` (Argon2id), `is_anonymous`, `language`, `region`, `data_key_id`, `created_at`, `deleted_at` | Authentication, locale routing for helplines, and master key ID tracking. |
| `consents` | — | `user_id`, `kind`, `version`, `granted`, `created_at` | Append-only audit trail of consent grants and withdrawals. |
| `chat_sessions` | `memory_encrypted` | `id`, `user_id`, `language`, `ephemeral`, `memory_turns`, `memory_updated_at`, `created_at`, `ended_at` | Session listing and rolling-memory turn counters. |
| `messages` | `content_encrypted` | `id`, `session_id`, `role`, `emotion`, `risk_level`, `created_at` | Turn ordering, safety tier tracking, and aggregate emotion counts. |
| `mood_entries` | `note_encrypted` | `id`, `user_id`, `valence`, `energy`, `emotions`, `factors`, `created_at` | Forgiving streak calculation and numeric trend charts without decrypting notes. |
| `journal_entries` | `title_encrypted`, `body_encrypted` | `id`, `user_id`, `key_id`, `sentiment`, `emotion_scores`, `template`, `prompt_id`, `created_at`, `updated_at` | Key rotation tracking (`key_id`) and optional numeric sentiment/emotion scores (`store_emotions=false` sets both to `NULL`). |
| `assessment_results` | `answers_encrypted` | `id`, `user_id`, `instrument`, `total_score`, `band`, `created_at` | PHQ-9 / GAD-7 total and severity band history while keeping per-item answers encrypted. |
| `safety_events` | — (never stores text) | `id`, `user_id`, `session_id`, `risk_level`, `source`, `category`, `created_at` | Safety audit trail without creating a second copy of user text. |
| `refresh_tokens` | — | `id`, `user_id`, `family_id`, `token_hash` (SHA-256), `expires_at`, `revoked_at`, `replaced_by_id`, `created_at` | Refresh-token rotation and replay detection. |
| `exercise_completions` | — (Day 18 stores no free text) | `id`, `user_id`, `exercise_id`, `rating`, `note_encrypted` where present, numeric `duration`/counters, `created_at` | Progress history and "what helped" ratings. |
| `cbt_records` | `situation_encrypted`, `thought_encrypted`, `evidence_for_encrypted`, `evidence_against_encrypted`, `balanced_thought_encrypted` | `id`, `user_id`, `emotion`, `intensity_before`, `intensity_after`, `distortion_tags`, `created_at` | Day 19 thought records: the five free-text fields are ciphertext; the tag list and intensity numbers power the charts. |
| `plan_items` | — | `id`, `user_id`, `local_date`, `kind`, `title`, `why`, `status`, `created_at` | Day 21 plan: `title`/`why` are generated rule text, not user words; kept plain so a plan is readable without a key. |
| `user_settings` | — | `id`, `user_id`, `timezone`, `quiet_hours_*`, `preferred_tags`, `reminder_*` | Preferences the planner reads; contains no personal content. |
| `privacy_settings` | — | `user_id`, `store_chat`, `store_emotions`, `retention_days`, `created_at`, `updated_at` | Day 22: the three switches that decide what the app may keep and for how long. Values only — the row says nothing about its owner's content. |
| `deletion_receipts` | — | `id`, `account_ref` (32-char keyed HMAC), `requested_via`, `rows_deleted` (JSON counts), `created_at` | A content-free proof that a deletion happened. Deliberately has no user id, e-mail or free-text column; schema tests fail CI if one is added. |

---

## 3. Day 17 AES-256-GCM envelope encryption & key rotation

Day 17 upgrades the Day-3 single-key Fernet stub to full **AES-256-GCM envelope
encryption** (`backend/app/services/journal/crypto.py`, see
[ADR 0017](adr/0017-encrypted-journal-and-envelope-encryption.md)):

### 3.1 Key hierarchy

1. **Master Key Encryption Key (`KEK`) — `FIELD_ENCRYPTION_KEY`**:
   - Held in environment configuration (never in the database).
   - Managed by `MasterKeyRing`, which supports a single key or a
     comma-separated rotation keyring (`"<active_id>:<active_b64>,<old_id>:<old_b64>"`)
     plus `FIELD_ENCRYPTION_PREVIOUS_KEYS`.
2. **Per-User Data Encryption Key (`DEK`) — `users.data_key_encrypted`**:
   - Each user receives a random 32-byte (256-bit) `DEK` generated from
     `os.urandom(32)`.
   - The `DEK` is wrapped with AES-256-GCM under the active master key using a
     fresh 96-bit nonce and Associated Data:
     `manovia:dek:v1|user_id=<user_uuid>|key_id=<master_key_id>`.
   - Stored in `users.data_key_encrypted` (`MDEK\x01` header) alongside
     `users.data_key_id`.
3. **Per-Field Ciphertext (`MENV\x01`) — `encrypt_field` / `decrypt_field`**:
   - Each journal `title` and `body` is encrypted with the user's 256-bit `DEK`,
     a fresh 96-bit random nonce (`os.urandom(12)`), and Associated Data:
     `manovia:field:v1|user_id=<user_uuid>|field=<field_name>`.
   - Because `(user_id, field_name)` is authenticated by AES-GCM's 128-bit tag:
     - Flipping any single byte in the header, wrapped DEK, nonce, ciphertext,
       or tag fails authentication and raises `DecryptError`.
     - Copying a ciphertext blob from User A's row into User B's row fails AAD
       verification and raises `DecryptError`.
     - Swapping `title_encrypted` and `body_encrypted` on the same row fails
       AAD verification and raises `DecryptError`.

### 3.2 Migrating from Day-3 Fernet and rotating master keys

- **Alembic revision `0004`** (`backend/alembic/versions/0004_envelope_encryption_and_journal.py`)
  provisions wrapped per-user DEKs on `users` and migrates any legacy Day-3
  Fernet ciphertexts in `journal_entries`, `mood_entries`, `chat_sessions`, and
  `messages` into `MENV\x01` AES-256-GCM envelopes.
- **Key rotation script** (`scripts/rotate_encryption_keys.py`):
  1. Generate a new 32-byte base64url master key and prepend it to
     `FIELD_ENCRYPTION_KEY` with a new key ID (e.g.
     `FIELD_ENCRYPTION_KEY="v2:<new_key>,v1:<old_key>"`).
  2. Run `python scripts/rotate_encryption_keys.py` (or pass `--new-key` and
     `--old-key`).
  3. The script unwraps every user's DEK, mints fresh wrapped DEKs under `v2`,
     and re-encrypts all personal columns under `v2` (`users.data_key_id = 'v2'`,
     `journal_entries.key_id = 'v2'`).
  4. Remove `v1:<old_key>` from `FIELD_ENCRYPTION_KEY`; only `v2` is needed
     thereafter.

---

## 4. Why server-side search is impossible on ciphertext (client-side search)

Every `title_encrypted` and `body_encrypted` value is encrypted with AES-256-GCM
using a fresh 96-bit random nonce (`os.urandom(12)`). Encrypting the exact same
word twice produces two completely unrelated byte strings. As a result:

- SQL `LIKE '%word%'`, `ILIKE`, PostgreSQL `tsvector` full-text indexes, and
  SQLite FTS5 **cannot** match words inside `title_encrypted` or
  `body_encrypted`.
- Building a server-side inverted index of words would leak the vocabulary of a
  user's journal in the database, defeating encryption at rest.
- Therefore, **journal search is implemented client-side in browser memory**
  (`filterEntriesClientSide` in `frontend/src/features/journal/markdown.ts`):
  the browser fetches the signed-in user's decrypted entries over HTTPS and
  filters them locally in the tab. Search queries are never sent to the server.

---

## 5. In-memory processing on journal write (safety & optional emotion scores)

When a user creates (`POST /api/v1/journal`) or updates (`PUT /api/v1/journal/{id}`)
a journal entry, the plaintext exists in backend request memory only long
enough to perform two operations before encryption:

1. **Mandatory safety screening (`AGENTS.md` rules 1, 2, 4, 6)**:
   - Runs `run_ensemble` (deterministic multilingual crisis rules engine +
     additive ML backstop) and `escalator.plan` on `title + "\n" + body`.
   - If the safety engine fails, the request **fails closed with HTTP 503** and
     nothing is written to the database.
   - If the entry triggers `HIGH` or `IMMINENT` risk, the entry is still
     encrypted and saved, a text-free `SafetyEvent` row is recorded, and
     verified crisis helplines from `backend/app/content/helplines.json` are
     returned in `metadata.resources` and `crisis`.
2. **Optional numeric sentiment & emotion scoring (`store_emotions`)**:
   - When `store_emotions=true` (default), `EmotionAnalyzer.analyze` computes
     numeric `sentiment` (`[-1.0, 1.0]`) and `emotion_scores`
     (`dict[str, float]`) in memory and stores **only the numbers** in the
     clear on `journal_entries`.
   - When the user unchecks **"Save numeric emotion & sentiment scores"**
     (`store_emotions=false`), the analyzer is skipped and both `sentiment` and
     `emotion_scores` are stored as `NULL`.

---

## 6. Threat model: what IS and is NOT protected

### What envelope encryption protects against

| Threat | Protected? | Mechanism |
| --- | --- | --- |
| Leaked SQLite file, PostgreSQL disk snapshot, or backup archive | **Yes** | All personal text columns are `MENV\x01` AES-256-GCM ciphertexts; `FIELD_ENCRYPTION_KEY` is not in the database. |
| Read-only SQL injection or unprivileged DB read access | **Yes** | Only ciphertext blobs and wrapped DEKs (`MDEK\x01`) are stored in tables. |
| Attacker with DB write access swapping ciphertexts between users | **Yes** | AES-GCM Associated Data binds every ciphertext and wrapped DEK to `user_id`; decryption under another user raises `DecryptError`. |
| Attacker with DB write access swapping `title_encrypted` and `body_encrypted` | **Yes** | AES-GCM Associated Data binds `field_name` (`"title"` vs `"body"`). |
| Byte-level tampering or bit-flipping in database storage | **Yes** | 128-bit AES-GCM authentication tag rejects any modified byte with `DecryptError`. |
| Accidental plaintext leakage into application logs | **Yes** | Structlog processor redacts sensitive field names (`title`, `body`, `note`, `content`, keys), and endpoints log only IDs, counts, and risk tiers. |
| Compromise of a retired master key after rotation | **Yes** | `scripts/rotate_encryption_keys.py` mints fresh per-user DEKs and re-encrypts all ciphertexts under the new master `key_id`. |

### What envelope encryption does NOT protect against (residual risks)

1. **Compromise of the running application server with `FIELD_ENCRYPTION_KEY` in memory.**
   Because encryption and decryption happen on the backend server and the master
   key (`FIELD_ENCRYPTION_KEY`) is loaded in the backend process environment,
   an attacker who gains root/process-memory access to the live API server (or
   steals both the database *and* the environment secrets simultaneously) can
   unwrap user DEKs and decrypt entries.
2. **Malicious server operator.**
   Envelope encryption is **at-rest encryption**, not client-side end-to-end
   encryption (E2EE). A server operator who controls the running backend process
   could inspect plaintext in memory during `POST`/`PUT`/`GET` requests.
   *Why not client-held-key E2EE?* `AGENTS.md` safety rule 1 requires server-side
   crisis detection on user text so we can surface verified helplines reliably,
   and client-held passphrases mean a forgotten passphrase permanently locks a
   distressed user out of their journal.
3. **Metadata inference.**
   Row timestamps (`created_at`, `updated_at`), ciphertext byte length
   (which correlates with entry character length), `template`, `prompt_id`, and
   — unless disabled via `store_emotions=false` — numeric `sentiment` and
   `emotion_scores` remain visible in the database without the master key.
4. **In-memory plaintext lifetime in Python.**
   CPython `str` and `bytes` objects are immutable and garbage-collected rather
   than deterministically zeroed in RAM; a core dump taken mid-request could
   contain plaintext fragments.
5. **Client-side XSS / shared unlocked device.**
   Decrypted entries are rendered in the user's browser and access tokens are
   held in web storage (see ADR 0005), so someone with physical access to an
   unlocked browser tab or an XSS vulnerability could read the user's entries.

---

## 6. Day 22: STRIDE threat model

Section 6 above answers "can they read the ciphertext?" This section is the
wider question the Day-22 brief asks for: **who can do what to this system, and
what stops them?** It is written as a STRIDE table because that is the format
that forces every category to be answered rather than only the interesting ones
(tampering, which the encryption already handles well).

Scope: the API (`backend/app`), the web client (`frontend/src`), the single
SQLite-or-PostgreSQL database, the operator's environment, and the browser's
storage. Out of scope: the hosting provider's hypervisor, the user's own device
malware, and the safety *content* decisions (see `docs/ethics-and-limitations.md`).

### 6.1 Assets

| # | Asset | Where it lives | Why it matters |
| --- | --- | --- | --- |
| A1 | What the user wrote: chat turns, conversation memory, journal title/body, mood notes, CBT fields | `*_encrypted` columns, AES-256-GCM | The most intimate data in the system; disclosure harms the person, not the company. |
| A2 | Per-user DEK and the master KEK (`FIELD_ENCRYPTION_KEY`) | `users.data_key_encrypted`; environment | Whoever holds both holds A1. |
| A3 | Account credentials: `password_hash` (Argon2id), refresh-token hashes, access tokens | `users`, `refresh_tokens`, browser storage | Account takeover converts one disclosure into ongoing access. |
| A4 | Safety audit trail: `safety_events` (risk level, source, category, timestamp) | `safety_events` | Reveals *that* someone was in crisis even though it never stores their words. |
| A5 | Consent and privacy state: `consents`, `privacy_settings` | database | The record that says what the user allowed; if it can be forged, consent is meaningless. |
| A6 | The running service itself: API, web bundle, dependency tree, CI pipeline | images, lockfiles, workflows | An attacker who controls the build or the process reads A1 in the clear. |
| A7 | The export file (A1, decrypted, outside the encrypted store) | user's downloads folder / browser + server memory during the request | Plaintext leaves the system by design; the perimeter moves to the device. |

### 6.2 STRIDE table

| Threat | Category | Target | Mitigation as implemented | Residual risk |
| --- | --- | --- | --- | --- |
| Stolen database file or backup | Information disclosure | A1 | `MENV\x01` AES-256-GCM per-field ciphertext; KEK lives in the environment, never in the database; per-user DEK wrapped with AAD binding `user_id` and `key_id`. | Metadata inference (timestamps, ciphertext length, numeric emotion scores) — §7.3 of section 6 above. A stolen DB **plus** the running env secrets is a full compromise. |
| Live server compromise / malicious operator | Information disclosure | A1, A2, A7 | Keys are loaded from env only, never persisted; no `print`/log of plaintext (§1.2); redaction is enforced by a structlog processor, not by convention. | At-rest encryption is not E2EE: plaintext exists in process memory during a request. Accepted deliberately — server-side crisis detection requires it (ADR 0017 §"not protect against"). |
| SQL injection | Tampering / Information disclosure | A1–A5, DB | Every query goes through SQLAlchemy Core/ORM with bound parameters; no f-string SQL anywhere; `tests/security/test_injection.py` (81 cases) drives hostile tokens through ids, emails, journal text, mood notes, chat, filters, query params, headers. | A future raw-SQL escape hatch would not be covered. Reviewer rule: raw SQL is a security change, not a performance tweak. |
| XSS via user content (chat, journal, mood, CBT) | Tampering / Information disclosure | A3, A7 | React text nodes everywhere; `dangerouslySetInnerHTML` appears **nowhere** in `frontend/src` (asserted by a unit test and re-verified by `grep -r`); journal markdown is a text renderer, not an HTML one; production CSP `script-src 'self'` with no inline script; `e2e/xss.spec.ts` round-trips `<img src=x onerror=alert(1)>` through a real browser in three surfaces. | A DOM-based sink introduced later in a dependency could reopen this. The CSP is the backstop, and it is verified against the built bundle, not a copy of the policy. |
| XSSI / drive-by framing | Information disclosure | A1 | `frame-ancestors 'none'` + `X-Frame-Options: DENY`; `default-src 'none'` on API JSON; `X-Content-Type-Options: nosniff`; `Cache-Control: no-store` on API responses. | None known for the current response shapes. |
| CSRF on state-changing requests | Tampering | A3, A5, A1 | Bearer token in the `Authorization` header (never a cookie), so a cross-site form post carries no credential; plus an `OriginGuard` middleware that refuses unsafe methods from a non-allowlisted `Origin` with 403 `origin_not_allowed` (missing `Origin` is allowed for non-browser clients; `Origin: null` is refused). | A deployment that sets `ALLOWED_ORIGINS=*` or disables `ORIGIN_CHECK_ENABLED` reopens this. Both are documented as production-hostile settings. |
| Token replay / refresh-token theft | Spoofing / Elevation | A3 | Refresh tokens are stored only as SHA-256 hashes, rotate on every use, and a replayed old token revokes the whole family (`refresh_token_reused`). Access tokens are short-lived HS256. Login and deletion both count toward a 5-attempt lockout that doubles to 3600 s. | Refresh tokens sit in `localStorage` (known issue, §7.1): an XSS that manages to run would steal them. Nothing else in the design assumes localStorage is safe. |
| Forged or replayed consent | Repudiation | A5 | Consent rows are append-only (`granted`, `version`, `created_at`) and checked per request against the *current* document version in `backend/app/content/consent_documents.json`; changing the version re-blocks every personal surface with 403 `consent_required`. | Consent is per-account, not per-device: anyone with a valid session acts as the user. |
| Account enumeration | Information disclosure | A3 | Auth errors are uniform (`invalid_credentials`) for unknown email and wrong password; the reset path is not built yet. Registration remains distinguishable (a real product constraint). | Enumeration via registration is accepted and listed as a known issue. |
| Abuse and resource exhaustion (DoS) | Denial of service | A6 | Per-request body ceiling (413 `payload_too_large`, 256 KiB default), per-request timeout (503 `request_timeout`, 30 s, with `/stream` exempt because SSE is long by design), per-IP rate limits (`/auth` 10/min, `/api/` 120/min), an LLM token/cost ceiling, and an export row ceiling. | Limits are **per process**: with multiple workers the effective rate is per worker. No reverse-proxy-level limit ships in the compose file. Listed as a known issue. |
| Deleting the wrong account / irreversible mistake | Repudiation / DoS of the user's own data | A1, A5 | Deletion needs the exact confirmation string **and** re-authentication, is rate-limited against brute force, and the UI gates the button behind typing `DELETE`. The response is explicit that the action is irreversible. | A user who confirms and regrets cannot be helped. Recorded as an intentional trade-off in ADR 0022, not an oversight. |
| Malicious or compromised dependency | Tampering | A6 | Base + dev requirements pinned to exact verified versions; `npm ci` from a committed lockfile; CI actions pinned to commit SHAs; `pip-audit` blocks on any advisory and `npm audit --omit=dev --audit-level=high` blocks on high/critical; `gitleaks` blocks on a detected secret with an offline stand-in (`scripts/scan_secrets.py`) for machines that cannot run the binary. | Pinning freezes *known-bad* versions out but does not stop a compromised *release* being pinned in later; the audit gates are what catch that, and they only know published advisories. |
| Secret committed to the repository | Information disclosure | A2, A3 | gitleaks in CI (blocking, full history); `scripts/scan_secrets.py` for local/offline runs; `.env` and `.env.*` gitignored with `.env.example` as the only tracked template; `.pre-commit-config.yaml` for the author's machine. | The offline stand-in is a weaker engine (no entropy at the file level, no baseline). Its gaps are printed on every run rather than implied away. |
| Reading another user's rows because of a missed filter | Elevation / Information disclosure | A1 | Every user-scoped query filters on the authenticated `user_id` derived from the token, never from the request body; the privacy routes are the newest place this is asserted (`tests/privacy/test_deletion.py` queries every table by `user_id` after a delete). | A single missing filter is a silent cross-account read; this is why the deletion test enumerates tables rather than sampling one. |
| Log or metrics leakage of user text | Information disclosure | A1, A4 | Two independent mechanisms: `core/logging.py` recursively replaces sensitive keys (`message_text`, `content`, `title`, `body`, `note`, `text`, `prompt`, `query`, credentials, keys) with a marker, and endpoints log ids/counts/risk tiers only. `tests/security/test_log_sentinels.py` drives chat + journal + mood + CBT + export with sentinel strings and asserts none appear in captured logs, exception reports or metrics. | A new field name that is not on the blocklist is not caught by the processor — the sentinel test is what catches that class of mistake, and it must be extended when a new free-text field ships. |
| Tampering with ciphertext (swap between users, field confusion, bit flip) | Tampering | A1 | AES-GCM AAD binds `user_id` **and** `field_name`; a swapped or flipped blob raises `DecryptError`. The export path turns that into a visible `decryption_failed` row instead of a silent omission. | Availability, not confidentiality: rows made unreadable by tampering are unrecoverable without a backup. |
| Forged deletion receipt | Repudiation | A5 | `account_ref` is a keyed HMAC over the user id under the deployment secret; the table has no content column; the schema test fails if a free-text column is added. | An operator who holds the secret can recompute an `account_ref`, i.e. link a receipt to a user id. That is the intended support capability, and it is why the secret is a deployment key. |
| Unsafe rendering of the export inside the app | Tampering | A7 | The export is delivered as a download (`Content-Disposition` attachment, `Accept: */*` client path); the app never renders file contents back into the DOM. | The user's own machine may open the ZIP in a tool that renders it. Out of scope, stated in the export README. |

### 6.3 The three biggest remaining privacy risks (honest ranking)

These are the risks this build does **not** solve, ordered by how much harm they
would cause if they happened. They are repeated in `PROGRESS.md` and in the Day-22
verification document so they cannot be lost in a document nobody opens.

1. **Refresh tokens live in `localStorage` (A3 → A1).** The design holds bearer
   tokens in web storage, so any successful XSS escalates from "runs script in
   the page" to "keeps the account" — the token survives a reload. Today there is
   no `dangerouslySetInnerHTML`, the CSP forbids inline script, and the XSS e2e
   passes; that is defence in depth, not immunity. The fix is a
   `HttpOnly` + `SameSite=Strict` refresh cookie with CSRF protection, which is a
   cross-cutting auth change (it inverts the CSRF story in §6.2) and was out of
   Day 22's scope. **This is the single highest-value security change left.**
2. **Single-process SQLite is the only configuration actually exercised (A6).**
   Rate limits, lockout counters and the in-memory ephemeral chat store are all
   per process. Deployed with multiple workers or behind a load balancer, the
   effective limits multiply by the worker count, and rate limiting becomes
   advisory. PostgreSQL is supported in the code and by the migrations but has
   not been run here (no Docker in the sandbox), and no shared limiter (Redis)
   exists. Until then: run one worker, or accept that the limits are soft.
3. **Nobody has reviewed the safety and helpline content clinically (A4, and see
   `docs/ethics-and-limitations.md`).** The product's central promise is that a
   person in crisis gets a correct, verified, culturally-appropriate response.
   The crisis rules engine has 1.000 recall on the dev split and the helplines
   are validated against a JSON schema at import — but no clinician, no native
   Bengali/Hindi speaker and no Indian crisis-line operator has reviewed the
   copy. A schema-valid helpline number that is wrong is worse than no number.
   This is not fixable with more tests and needs a human.

Two adjacent, lower-severity debts: the safety ML still over-escalates on exam
phrases (`exam`, `test`, `cut off`), and there is no purge job turning
`users.deleted_at` (the pre-Day-22 soft-delete column) into a real deletion, so
an account deleted before Day 22 keeps its row and its unique email. Accounts
deleted through `DELETE /api/v1/privacy/account` are hard-deleted in full.

---

## 6a. Day 25: what the device keeps (offline support)

Day 25 made the app work offline, which means some things now live on the
reader's device as well as (or before) the server. Each is listed with its
lifetime, because "it's only on your phone" is still a copy.

| What | Where | Content | Cleared when |
| --- | --- | --- | --- |
| App shell (HTML, JS, CSS, icons, manifest) | Cache Storage (`workbox-precache-…`) | Code only, no personal data | Replaced on every update; on site-data clear |
| Crisis helplines | Cache Storage `manovia-helplines-v1` | The public `GET /crisis/resources` answer (from `helplines.json`, with its `last_verified`) | 30-day expiry; refreshed whenever online (network-first) |
| Exercise catalogue | Cache Storage `manovia-exercises-v1` | The public exercise definitions | 60-day expiry; refreshed in the background |
| Queued mood check-ins | `localStorage` `manovia.mood.outbox.v1` | Scales, tags **and the note, in plain text**, plus a random `client_id` and the time it was made | Sent and removed on reconnect; on sign-out / onboarding restart; unsendable items stay listed until discarded |
| Journal draft (one) | `localStorage` `manovia.journal.draft.v1` | Title and body **in plain text** while unsaved | Removed when the server confirms a save; on discard; on sign-out; after 30 days |

**Never cached by the service worker:** any authenticated request (one that
carries `Authorization`), and every personal endpoint — journal, mood, chat,
plan, insights, privacy export, exercise history. The rule is code
(`frontend/src/pwa/swRoutes.ts`) and is asserted by `swRoutes.test.ts`. The
worker never holds the session token and does no background sync, so it cannot
send anything as anybody.

**Residual risks, stated plainly:**

1. **Plain text on the device.** A queued check-in note or an unsaved journal
   draft is readable by anyone who can open the browser's storage on that
   device until it is sent, saved or discarded. That is the same trust boundary
   as the session token already in `localStorage`, and it is the price of not
   losing words written offline. Encrypting them locally was rejected: the key
   would have to live on the same device (see ADR 0025).
2. **Shared devices.** Signing out (or restarting onboarding) clears the queue
   and the draft. A queued check-in made under one account is never sent under
   another (`account_changed`).
3. **Stale helplines.** An offline copy can be up to 30 days old. The dialog
   says it is the saved copy, and every entry shows its `last_verified` date.

## 7. Manual checks this sandbox could not perform

Stated because an unverified control presented as verified is worse than a known
gap. Someone with the environment must do these by hand:

1. **Run the stack in Docker.** `docker compose up` was never executed here (no
   docker binary). That means `docker/security-headers.conf` was verified by
   serving the built bundle with those exact headers from a local server
   (`frontend/scripts/verify-prod-csp.mjs`), but **not** through nginx itself, and
   `docker/nginx.conf`'s `try_files`, gzip and cache behaviour are unverified.
2. **Repeat the deployment on PostgreSQL.** Migrations `0001`–`0008` have only
   ever run against SQLite. The cascade in `ON DELETE CASCADE` is native in
   PostgreSQL and PRAGMA-driven in SQLite, so the deletion path is the one to
   watch first.
3. **Confirm the retention job under a real scheduler.** The CLI is verified
   (dry run, real run, idempotent re-run) but nothing invokes it on a timer.
4. **Point the app at a real LLM provider.** Everything here ran against
   `LLM_PROVIDER=fake`. The redaction and token-guard path that protects a real
   provider is unit-tested but has never carried a real conversation.
