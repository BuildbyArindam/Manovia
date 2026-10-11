# Privacy & Threat Model

This document states, in plain terms, what Manovia stores, how it is protected,
what never leaves the user's device or the database, and — just as importantly —
what our encryption **does and does not protect against**. It is a living
document updated as new surfaces land (Day 3 data layer, Day 4 auth & consent,
Day 6 NLP redaction, Day 8–9 crisis engine, Day 11–14 chat & output guard,
Day 15 mood check-ins, Day 17 encrypted journal & envelope encryption).

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
   dependent row in one transaction.

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
