# ADR 0022 — Privacy controls: export everything, delete everything, keep nothing secret from its owner

Date: 2026-10-12 · Status: accepted

## Context

Days 3–21 built encryption, consent gating and a safety gate around the user's
words. Day 22 is the other half of that promise: the person whose data it is
must be able to **get it out** and **take it away**, and the app must be able to
say plainly what it keeps. Three forces shaped the design.

1. **A privacy policy nobody can execute is marketing.** "We respect your
   privacy" is unfalsifiable. "Your words are AES-256-GCM ciphertext, this key
   is not in the database, here is a ZIP of everything we hold" is checkable.
2. **Deletion must be a deletion, not a tombstone with extra steps.** A soft
   delete that leaves ciphertext behind is a promise to forget that the database
   did not keep. And a "deletion receipt" that quotes the deleted content would
   create the very copy deletion was meant to destroy.
3. **Retention has to be a mechanism, not a sentence.** "We keep your data only
   as long as needed" means nothing without a job that reads a per-user setting
   and deletes rows older than it.

Two tensions were unavoidable and are resolved explicitly below: an export is a
**decrypted copy of the most sensitive data in the system leaving the server**
(the exact thing the threat model works to prevent), and a deletion request is a
**one-way, unrecoverable action** that a distressed person may make in a bad
moment.

## Decision

### 1. Export: decrypted, for the owner, in one stream

`GET /api/v1/privacy/export?format=zip|json` returns the authenticated user's
data as a ZIP (`manovia-export/`) or the same payload as a single JSON document.

- **Decrypted for the owner.** `format_version = "manovia-export-1"` and
  `contents_are_decrypted = true` are stated in `manifest.json`, because a
  user-visible export that contained ciphertext would be useless.
- Every table the app writes is included: `profile`, `consents`,
  `privacy-settings`, `chat`, `journal`, `mood`, `assessments`, `exercises`,
  `cbt`, `plan`, `safety-events`.
- **Ceilings are declared, never silent.** `privacy_export_max_rows` (default
  5000) caps each table; `counts` and `truncated_tables` are in the manifest so
  a person can see that a table was cut rather than assume it was empty.
- **A row that cannot be decrypted is reported, not dropped.** If a
  ciphertext fails (`DecryptError`, a rotated-away key), the row appears with
  `null` content and a `decryption_failed` marker and the id lands in
  `unreadable_rows`. Silently omitting it would make the export lie by
  omission — the one failure mode a data export must not have.
- **`README.txt` explains the format**, because the file outlives the app that
  produced it.

### 2. Deletion: one cascade, a content-free receipt

`DELETE /api/v1/privacy/account` with `{"confirm": "DELETE", "password": …}`.

- **Hard delete, one transaction.** Every child table references `users.id`
  with `ON DELETE CASCADE`, so deleting the `users` row destroys the wrapped
  per-user DEK and every dependent row. No tombstone, no `deleted_at`-only
  state, no "recoverable for 30 days".
- **Re-authentication is required.** A guest account confirms with the
  confirmation string; an account with a password must also present that
  password (403 `reauth_required` / `invalid_credentials`). A stolen access
  token is not sufficient to destroy an account.
- **The receipt records that it happened, never what was in it.** `receipt_id`,
  `account_ref`, `requested_via`, `rows_deleted`, `total_rows`, `created_at`.
  The receipt table has no content column and never will.
- **`account_ref` is a keyed HMAC**, 32 hex characters, derived from the user id
  under the deployment secret — so a support conversation can reference "this
  deletion" without the receipt becoming a lookup table for user ids.
- **Failure is loud and rate-limited.** A wrong password counts toward the same
  lockout policy as login (5 attempts → 429 `account_locked`).
- **Afterwards the account is gone for every credential**: `/auth/me`,
  `/auth/refresh` and data routes return 401 `account_deleted`, not 404, so a
  client never mistakes deletion for a transient error.

### 3. Privacy settings: three switches, stored per user, defaulting to on

`GET`/`PATCH /api/v1/privacy/settings` read and write `privacy_settings`:
`store_chat`, `store_emotions` (sentiment scores), `retention_days`.

- A user with no row gets documented **defaults** (store on; retention 365), not
  a 404, so the Settings screen is never empty on a fresh account.
- `PATCH` honours `model_fields_set`: sending `{"store_chat": false}` does not
  silently reset the other two. An empty patch is `422 empty_update` rather than
  a no-op success.
- `retention_days` is bounded to 7–3650 with presets `[30, 90, 180, 365]`.

### 4. Retention: a real job with a dry run

`app/services/privacy/retention.py` — `plan_retention()` (what would go) and
`apply_retention()` (what goes), plus `scripts/apply_retention.py` and
`make retention` for the operator.

- The cutoff is `now - retention_days`, **exclusive**, evaluated in UTC.
- Only users with an explicit `privacy_settings` row are considered: an account
  that never opened the screen is not silently put on a deletion schedule.
- Messages die by their **own** `created_at`, not their session's, so a long
  conversation is not truncated because it started long ago.
- `--dry-run` is the default in the Make target's documentation (`DRY_RUN=1`)
  and the job is idempotent: a second run deletes zero rows.
- **The job is not scheduled.** There is no cron, no worker, no background
  thread — see Consequences.

## Alternatives considered

- **Encrypt the export with a passphrase the user chooses.** Safer in transit,
  and a support nightmare: a lost passphrase means the export is unreadable
  exactly when someone is trying to leave with their data. The transport
  (HTTPS, authenticated session, `Cache-Control: no-store`) is the protection,
  and the file is handed to the person who already has access to the plaintext
  in the app.
- **Stream the export table-by-table to avoid holding it in memory.** The right
  long-term answer for a large account, and a bigger change than one day
  allows: it needs a streaming ZIP writer and a resumable manifest. The row
  ceiling plus explicit `truncated_tables` is the honest interim position, and
  it is measured against the real ceiling rather than assumed.
- **Soft delete with a 30-day recovery window.** Kinder to someone who deletes
  in a bad moment, and it contradicts the feature: the point is that the data is
  gone. The confirmation step and the plain-language copy in the UI are the
  protection against an impulsive deletion, not a hidden copy on disk.
- **A `[[retention]]` cron in the container.** Would fire in the web process,
  race with a multi-worker deployment, and run exactly when nobody is watching.
  Making it a CLI an operator (or a real scheduler) invokes keeps it observable
  and idempotent; the gap is stated rather than papered over with a thread.

## Consequences

- **The export is the weakest link in the at-rest story, by construction.** It
  produces plaintext outside the encrypted store, on disk in the user's
  downloads folder. This is intentional and bounded: owner-authenticated,
  `no-store`, one request at a time per user, and it is the user's own data. The
  threat model now lists "export file left on a shared device" as a residual
  risk, which is a user-education problem, not an encryption one.
- **Deletion is irreversible by design**, so the API requires an explicit
  confirmation string *and* re-authentication, and the UI gates the button
  behind typing `DELETE`. A user who deletes and changes their mind cannot be
  helped by the backend; that trade-off is recorded here on purpose.
- **Retention depends on someone running the job.** Until a scheduler exists,
  "retention" is a setting with a working implementation and no clock attached.
  This is listed as a known issue in `PROGRESS.md`, not described as automatic.
- **The receipt table is the one place a deleted user is still referenced.**
  `deletion_receipts` keeps an HMAC `account_ref`, the counts and the timestamp
  after the cascade. No content, no email, no recoverable id — and it exists so
  a deletion can be *proven* to have happened.
- **Auditability follows the tests**: `tests/privacy/` covers export shape and
  decryption, deletion reach across every table, retention cutoffs (including
  idempotence), and the settings contract.
