# 0014 — Deterministic output guard and saved-chat memory

Date: 2026-10-11
Status: Accepted

## Context

The Day 11 orchestrator had an `OutputGuard` seam, but its default implementation
was pass-through. The product rule is stronger: a generated reply must be checked
before it is persisted or sent, including streamed replies. Day 14 also asks for a
short, user-visible memory of earlier turns, but only when the user has explicitly
chosen a saved chat (`store_chat` consent).

Two failure modes shaped the design:

1. **Unsafe text must never leak while a stream is being checked.** A whole reply
   is needed to evaluate length, language and context-sensitive rules. Checking
   token-by-token after tokens are on the wire would make a rejection cosmetic.
2. **Memory must not become an inferred profile or a second plaintext transcript.**
   An LLM-written summary can invent facts, while a non-redacted summary can copy
   identifiers into a new field. Deletion must not be undone by the next turn.

## Decisions

### 1. YAML-backed deterministic checks; no judge model in this milestone

`app/services/safety/output_guard.py` compiles `app/content/safety/output_patterns.yaml`
for diagnosis, medication, self-harm methods, identity claims, outcome promises,
isolation/dependence, length, language and the MEDIUM+ check-in requirement. The
checks use the same case-folded, punctuation-normalised projection as the input
safety rules. The pattern file also owns pre-written safe replies in English, Hindi
and Bengali; native-speaker and clinician review remains outstanding.

A deterministic table is preferred to a second LLM call: it is reviewable, offline,
reproducible, and cannot turn an unreliable judge into the safety boundary. A
future judge may only be an additional check behind an explicit configuration flag;
it cannot replace these rules. The known cost is vocabulary coverage: an unsafe
paraphrase that is absent from the file can pass, so additions require a reviewed
regression case.

### 2. Reject → one strict regeneration → local safe template

A passing reply proceeds unchanged. On a rejection, the orchestrator records each
closed category code (never matched text), retries the same provider once with a
stricter system instruction, and checks the new complete reply. If the retry is
unsafe, empty or unavailable, the orchestrator uses the locale-specific local safe
reply. The first and second rejection both create `SafetyEvent(source=llm_output)`
rows; the new nullable `category` column is a closed enum, so the audit trail can
say `diagnosis` without storing a fragment of the response.

The output guard fails closed if its own check raises. Guard and audit-store errors
are logged as exception types and categories only; model output is never included.

### 3. Buffer all model streams until the complete reply passes

The production guard declares `requires_full_text = true`. The orchestrator buffers
the first stream, performs the check and any retry, then releases only the accepted
reply or local safe template. `final.reply` remains authoritative. This trades
first-token latency for the guarantee that an unchecked sentence cannot be seen.
The passthrough implementation remains a test double and is not selected by the
production builder.

### 4. Extractive rolling memory, local and bounded

Only a saved conversation whose `store_chat` consent is still active can create or
use memory. The summarizer takes the already-redacted **user** turn only; assistant
text is excluded. It appends short, extractive snippets without inferring traits,
conditions or preferences, and keeps the newest snippets within a conservative
UTF-8-byte token estimate of about 300 tokens. The text is included in the next
prompt as a JSON-escaped, explicitly untrusted background block; current user text
and all history are still redacted before leaving the process.

No extra model call is made to summarize. This avoids hallucinated memory and keeps
the feature offline, at the cost of a less polished summary. HIGH/IMMINENT crisis
turns do not update memory.

### 5. Encrypt at the repository boundary; tombstone deletion

`chat_sessions.memory_encrypted` stores the summary using the existing field cipher.
The owner can read it via `GET /api/v1/chat/memory`; `DELETE /api/v1/chat/memory`
clears all summaries and stamps `memory_deleted_at` on every existing saved chat.
Updates use a conditional SQL `UPDATE` that refuses a tombstoned session, including
when a delete races a reply. This makes deletion irreversible for those existing
sessions while leaving encrypted saved messages untouched (the Settings UI says so).
New chats can create memory under the user's existing saved-chat consent.

## Consequences

- A model output is no longer streamed until it passes the whole-text guard.
- `safety_events` gains an optional, constrained category field. Existing input
  events remain unchanged with `category = NULL`.
- `chat_sessions` gains nullable encrypted-memory and deletion-tombstone fields.
- Existing revision history stays linear; Alembic revision `0003` adds both changes.
- The user can inspect and erase memory in Settings; memory deletion does not erase
  the saved transcript, and deleting that transcript remains a separate operation.
- English, Hindi and Bengali safe replies and check-in phrases are present, but the
  translations and output patterns still need a clinician/native-speaker review.
