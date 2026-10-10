# 0011 — The chat orchestrator: one pipeline, and the order is the safety property

- Status: accepted (Day 11)
- Date: 2026-10-10
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0003 data layer and encryption](0003-data-layer-models-migrations-and-encryption.md),
  [0004 authentication and consent](0004-authentication-and-consent.md),
  [0006 emotion model](0006-emotion-model.md),
  [0008 crisis detection rules engine](0008-crisis-detection-rules-engine.md),
  [0009 safety ML ensemble](0009-safety-ml-ensemble.md),
  [0010 LLM abstraction](0010-llm-abstraction.md)

## Context

Days 3–10 built the parts: a consent-gated, encrypted data layer; a rules + ML
crisis gate with a deterministic response; an emotion analyser; a PII redactor;
an LLM provider chain that cannot fail. Day 11 is the first place they run in
sequence on one person's message. Nothing here is new capability. What is new is
the **order**, and the order is where the safety guarantees live:

- A crisis message must never reach a model — not even a model that would answer
  well — so the gate has to sit *before* every other step that could call out.
- Personal text must be redacted *before* it leaves the process, and the
  redaction must not be skippable by a later refactor.
- A person who has not opted in to saving their chats must leave no
  user-linked rows behind, yet the model still needs the conversation so far.

The diagram is in [docs/architecture.md](../architecture.md).

## Decisions

### 1. The pipeline, in this order, in one module

`app/services/chat/orchestrator.py` runs: **validate → rate-limit → input
safety → (HIGH/IMMINENT: stop) → redact → emotion → retrieval → prompt → LLM →
output guard → persist**. Each step is a small collaborator injected into
`ChatOrchestrator` (rules engine, ML classifier, escalator, redactor, emotion
analyser, retriever, guard, LLM), so a test can replace exactly one of them.

Crisis wins early and *completely*: at `allow_llm=False` the orchestrator
builds the Day 8 deterministic reply, persists, and returns. There is no later
step that can "also" call the model. The test `HIGH ⇒ LLM call count is 0` runs
against the orchestrator, the HTTP API and the stream, and fails if the gate is
disabled (checked by mutation, see PROGRESS.md).

### 2. Fail closed on safety, fail soft on everything else

| Step fails | Behaviour |
| --- | --- |
| Input safety (rules/ML/escalator raise) | `503 safety_unavailable`. **No LLM call.** A reply nobody screened is worse than no reply. |
| Redaction | No model call; the canned fallback is returned. |
| Emotion | `emotion = None`, no emotion hint; the reply proceeds. |
| Retrieval | `[]`. |
| LLM (error, empty reply, outage) | Fallback template (`response_type = "fallback"`, `degraded = true`). The Day 10 chain already ends in a canned link; the orchestrator also catches anything that escapes. |
| Output guard raises | The canned reply replaces the model text. |
| Persisting the assistant reply | Logged; the reply is still returned with `persisted = false`. |
| Persisting a crisis turn | Swallowed; the crisis response is always returned. |

The asymmetry is deliberate: the one failure that must stop the request is the
one where we cannot tell whether the person is in danger.

### 3. Ephemeral by default; `store_chat` only to save

`POST /chat/sessions` takes `save_history` (default `false`). Only
`save_history = true` requires the `store_chat` consent (403 `consent_required`
otherwise); everything else requires `ai_disclosure` + `terms` as before.

An ephemeral session lives in `EphemeralSessionStore`: process memory, sliding
30-minute TTL (`CHAT_EPHEMERAL_TTL_SECONDS`), a per-user cap (20), a global cap
with idlest-first eviction (`CHAT_EPHEMERAL_MAX_SESSIONS`) and a turn cap. It
writes **no `chat_sessions` and no `messages` rows**.

**What "leaves no trace" means exactly.** A MEDIUM-or-above message in an
ephemeral session still writes a `SafetyEvent` — with `user_id = NULL` and
`session_id = NULL`: level and source only, never text, linked to nobody. We
rejected a literal zero rows because the safety event stream is the only signal
for tuning the gate (Day 9) and it carries no identity. The same anonymous
event is written if consent is withdrawn after a saved session was created:
the turn is processed, nothing is stored (`persisted = false`).

### 4. Ownership is a 404, always

A session that belongs to someone else, does not exist, or has expired returns
the same `404 session_not_found` with the same body. A 403 would confirm that
the id exists. (`409 session_ended` is only reachable by the owner.)

### 5. What the model sees

- The system prompt (Day 10) plus a **context block** written by the
  application: an emotion hint (skipped for neutral or confidence < 0.35), a
  safety hint (MEDIUM → "include a gentle check-in"), and retrieval passages
  framed as reference material. Hints are in the *system* prompt, never in the
  user turn, so a user cannot forge them.
- A short window (`CHAT_HISTORY_TURNS`, default 10), **redacted**, beginning on
  a user turn. Turns that were crisis turns are dropped from the window and
  replaced by a "recent crisis" hint, so the model is told to be gentle without
  being shown the content.
- The user id never enters the prompt. Emotion analysis reads the *original*
  text (redaction would blank the signal); the retriever query and the model
  read the redacted text.

### 6. MEDIUM: the model gets a hint *and* the template's check-in is appended

A hint alone is not a guarantee, and the brief asks that a MEDIUM response
*contains* a check-in. So the last paragraph of the `check_in.medium` template
is appended to the model's text (and emitted as a final token on a stream), and
the helplines are returned in `metadata.resources`. Appending the whole template
was rejected: it repeats the model's own warmth.

### 7. Response contract

`metadata = {risk_level, emotion, response_type, resources, …}` with
`response_type` precedence **crisis > fallback > check_in > normal**, plus
`crisis` (template id, message, emergency instruction) for the UI's CrisisCard,
`check_in`, `degraded`, `region` and `persisted`.

Streaming is SSE: `event: token {"text"}` repeated, then
`event: final {reply, replaced, session_id, message_id, metadata}`.
`final.reply` is authoritative: if `replaced` is true (the output guard changed
the text) the client swaps it in. A crisis stream is one token and a final. An
`event: error` is used only for failures after streaming began; everything that
can be known before the first byte (auth, ownership, validation, rate limit,
safety outage) is a real HTTP status.

The output guard is a stub today (Day 14). Its interface already carries
`requires_full_text`; a guard that needs the whole reply makes the stream
buffer, check, and *then* release tokens, so Day 14 will not change the wire
format.

### 8. Short-lived database sessions, not the request's

Streaming responses outlive a request-scoped `yield` dependency. The
conversation objects therefore open their own session per operation. The user
message and its SafetyEvent are committed after emotion and before the model is
called, so a crash mid-generation still leaves the audit trail; the assistant
message is committed in `_finish`. A disconnect mid-stream does not persist
partial text.

### 9. Rate limit per user, after validation

`CHAT_RATE_LIMIT_PER_MINUTE` (default 30) via the existing in-memory limiter,
keyed by user id, answering `429` with `Retry-After`. It runs after validation
so malformed bodies do not burn quota, and **it also applies to crisis
messages** (the cap is high enough that a person in distress will not reach it;
the public `/crisis/*` endpoints are unaffected). `RATE_LIMIT_ENABLED=false`
disables it.

### 10. Logging

One structured `chat_turn` event per turn: a 16-character `text_sha`,
`text_length`, risk, emotion, `response_type`, `llm_called`, `allow_llm`,
`persisted`, `streamed`, latency. Never the text, the reply, or the user id in a
form that links to content.

## Alternatives considered

- **A LangChain-style graph.** More moving parts than a nine-step list needs,
  and the ordering would be data rather than code — harder to review as a safety
  property.
- **Request-scoped DB session for streaming.** Works until a client holds the
  stream open past the dependency's lifetime. Rejected (§8).
- **An LLM-based "should I call the crisis path" step.** The gate must be
  deterministic; Day 8/9 already settled this.
- **`403` for someone else's session.** Leaks existence (§4).
- **Cookies/`EventSource` for the stream.** `EventSource` cannot send an
  `Authorization` header; the UI (Day 12+) will `fetch()` the POST stream. The
  `GET` stream exists for `curl` and simple clients — see Consequences.

## Consequences

- The ordering is covered by tests that fail under mutation (gate disabled,
  redaction skipped, ownership unchecked).
- **`GET …/stream?message=` puts the message in the URL**, which proxies and
  access logs record. Prefer `POST`. The app's own access log is path-only; the
  risk is infrastructure outside our control. Documented as a known issue.
- **The ephemeral store and rate limiter are per-process.** With more than one
  worker, an ephemeral session id only works on the worker that created it and
  limits are per worker. A shared store (Redis) is parked.
- Concurrent messages in one session are not serialised; two simultaneous sends
  may interleave in history. Parked.
- There is no end-session or delete endpoint yet (`409 session_ended` exists for
  rows ended by other means). Parked.
- **The Day 9 ML backstop is much stricter than the rules-only engine, and now
  that it gates every chat turn its false positives are user-visible.** With the
  committed `crisis_v1` artifact and the shipped floors, many ordinary messages
  (7 of 11 in a hand-picked sample, including "hello") score a crisis mass ≥ 0.30
  and are answered with the crisis response (see
  PROGRESS.md, Day 11 verification and Known issues). That is the Day 9 trade
  working as designed — "the false positive is cheap" — but it was accepted for
  an assessment endpoint, not for a conversation. Day 11 does **not** retune
  safety thresholds; it needs a human decision.
