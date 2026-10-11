# ADR 0017: Encrypted journal, AES-256-GCM envelope encryption, and client-side search

- **Status:** Accepted (Day 17 encrypted journal)
- **Date:** 2026-10-11
- **Supersedes (in part):** Day-3 single-key Fernet stub in [ADR 0003](0003-data-layer-models-migrations-and-encryption.md)

## Context

Day 3 introduced a single-key `FernetCipher` behind `app/core/crypto.py` so every
`*_encrypted` column (`messages.content_encrypted`, `chat_sessions.memory_encrypted`,
`mood_entries.note_encrypted`, `journal_entries.title_encrypted`,
`journal_entries.body_encrypted`) was encrypted from day one, with the explicit
note in ADR 0003 that Day 17 would replace the single-key Fernet stub with
per-user envelope encryption, Associated Data (AAD), and master-key rotation.

Day 17 also ships the encrypted journal (`POST/GET/PUT/DELETE /api/v1/journal`
and `GET /api/v1/journal/prompts`). Three tensions shape the design:

1. **Ciphertext swapping and key rotation.** With a single flat key and no
   Associated Data, an attacker with database write access could copy User A's
   `body_encrypted` into User B's row (so User B decrypts User A's words) or
   swap `title_encrypted` and `body_encrypted`. Rotating a single key also
   requires knowing which master key encrypted each record.
2. **Safety screening and optional emotion trends vs. privacy.** `AGENTS.md`
   safety rule 1 requires every user-written text surface to run through the
   deterministic crisis rules engine and safety ensemble before storage, and
   the insights view benefits from numeric sentiment/emotion scores without
   ever keeping raw words in the clear.
3. **Search over encrypted entries.** Users expect to search their journal, but
   semantic encryption (AES-256-GCM with fresh 96-bit nonces) makes server-side
   SQL substring or full-text indexing impossible without decrypting every row
   on the server.

## Decisions

1. **AES-256-GCM envelope encryption (`app/services/journal/crypto.py`).**
   - Each user gets a random 256-bit (32-byte) Data Encryption Key (`DEK`)
     generated via `os.urandom(32)`.
   - The user's `DEK` is wrapped with AES-256-GCM using the active master Key
     Encryption Key (`KEK`) from `FIELD_ENCRYPTION_KEY`, tagged with the master
     `key_id` (default `"v1"`), and persisted in `users.data_key_encrypted`
     (`MDEK\x01` binary envelope) and `users.data_key_id`.
   - Wrapping a `DEK` binds `manovia:dek:v1|user_id=<uuid>|key_id=<kid>` as
     AES-GCM Associated Data (AAD), so a wrapped `DEK` copied to another user
     row fails authentication on unwrap.
   - Encrypting a field (`encrypt_field` / `decrypt_field`) uses the user's
     256-bit `DEK`, a fresh 96-bit random nonce (`os.urandom(12)`), and binds
     `manovia:field:v1|user_id=<uuid>|field=<field_name>` as AES-GCM Associated
     Data (AAD). Swapping ciphertexts across users (`user_a` -> `user_b`) or
     across columns (`title` <-> `body`) raises `DecryptError`.

2. **Self-contained `MENV\x01` envelope + `users.data_key_encrypted`.**
   - Each `MENV\x01` field ciphertext embeds the `key_id`, the user's wrapped
     `MDEK\x01` blob, the 12-byte field nonce, and the AES-GCM ciphertext +
     16-byte tag, while the canonical per-user wrapped `DEK` is also stored on
     `users.data_key_encrypted`.
   - This keeps `JournalRepository.title(entry)` and `JournalRepository.body(entry)`
     synchronous (no async lazy-load of the parent `User` required at read time)
     while still enforcing per-user `DEK` isolation and `(user_id, field_name)`
     AAD verification on every read.

3. **Alembic `0004` data migration and `scripts/rotate_encryption_keys.py`.**
   - Migration `0004_envelope_encryption_and_journal.py` adds
     `users.data_key_encrypted`, `users.data_key_id`, and
     `journal_entries.{key_id,emotion_scores,template,prompt_id,updated_at}`,
     and migrates any existing Day-3 Fernet tokens into `MENV\x01` envelopes.
   - `MasterKeyRing` accepts a comma-separated keyring in `FIELD_ENCRYPTION_KEY`
     (`"<active_id>:<active_b64>,<old_id>:<old_b64>"`) or
     `FIELD_ENCRYPTION_PREVIOUS_KEYS`.
   - `scripts/rotate_encryption_keys.py` unwraps every user's `DEK` and
     re-encrypts all `journal_entries`, `mood_entries`, `chat_sessions`, and
     `messages` under the new active `key_id`, after which the old master key
     can be removed from configuration.

4. **In-memory safety screening and opt-out numeric emotion scoring.**
   - On `POST /api/v1/journal` and `PUT /api/v1/journal/{id}`, the plaintext is
     screened in memory by `run_ensemble` + `escalator.plan`. If the safety
     engine raises, the write fails closed with `503` and nothing is written.
     If the entry is flagged as crisis (`HIGH` / `IMMINENT`), verified crisis
     resources from `helplines.json` are returned in `metadata.resources` and
     `crisis`, and a metadata-only `SafetyEvent` row is recorded.
   - When `store_emotions=true` (the default), `EmotionAnalyzer.analyze` runs
     in memory on the plaintext and stores only numeric `sentiment` (`float`)
     and `emotion_scores` (`dict[str, float]`) in the clear. When the user
     sets `store_emotions=false`, both columns are set to `NULL`.

5. **Client-side search in browser memory.**
   - Server-side search over AES-256-GCM ciphertext is impossible without
     decrypting every entry on the server. Instead, the frontend fetches the
     signed-in user's entries over HTTPS and filters them locally in browser
     memory (`filterEntriesClientSide` in `frontend/src/features/journal/markdown.ts`).

6. **Honest threat model disclosure.**
   - Both `docs/privacy-and-threat-model.md` and the in-app "Private by default"
     explainer explicitly state what envelope encryption does **not** protect
     against: a live compromise of the running application server where the
     attacker can read `FIELD_ENCRYPTION_KEY` from process memory or
     environment variables.
