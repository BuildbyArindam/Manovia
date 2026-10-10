# Manovia — Architecture

## Overview

Manovia is a privacy-first, safety-first mental wellbeing self-help companion.
It is NOT therapy, NOT diagnostic, NOT a crisis service. The architecture is
shaped by three non-negotiable safety rules (AGENTS.md):

1. Crisis/self-harm detection runs BEFORE any LLM call. HIGH/IMMINENT gets a
   deterministic, pre-written response. The LLM never improvises at high risk.
2. Every LLM output passes an output-safety check before reaching the user
   (Day 14).
3. Never log raw message text at INFO or above. Never send identifiers to the LLM.

## Backend stack

- Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic
- PostgreSQL in production, SQLite in dev/test
- Field-level encryption (Fernet) for message text, journal, mood notes
- JWT access (15 min) + rotating refresh (7 days, family + reuse detection)
- In-memory rate limiting + account lockout (Redis in parking lot)

## Data layer (Day 3)

```
users ──< consents (append-only, newest wins)
      ──< chat_sessions ──< messages (content_encrypted)
                        ──< safety_events (metadata only)
      ──< mood_entries (note_encrypted)
      ──< journal_entries (title/body encrypted)
      ──< assessment_results
      ──< refresh_tokens (hashed, family_id)
```

All child tables CASCADE from users. `safety_events` has no text column by design
— a whitelist test fails CI if one is added.

## Auth & consent (Day 4)

- Guest mode: anonymous user + real session, no email
- Upgrade attaches email/password to same user id (data survives)
- Consent documents are versioned JSON; `require_consent` checks current version
- Chat requires `ai_disclosure` + `terms`; persistent history additionally requires `store_chat`

## NLP service (Day 6)

```
text → detect_language (en/hi/bn/other + hinglish)
     → AnalyzerChain: HF model (lazy, thread-safe, circuit breaker + latency governor)
                    → KeywordFallback (9 emotions, negation/intensifiers)
                    → Sentiment (valence [-1,1])
     → EmotionResult {primary, scores[9], valence, arousal, provenance}
```

Cache: LRU keyed by hash(text), never stores plaintext. Model is optional extra.

## Crisis detection (Day 8 + Day 9)

Rules engine (181 patterns in YAML) → RiskAssessment (level + categories + codes, no text)
→ ML classifier (TF-IDF + logistic, calibrated, committed artifact) → ensemble

```
final = max(rules, ML-if-confident)   # never lowers, property-tested
```

Three raise bands: `ml.raised` (confident top class), `ml.crisis_mass` (P(high)+P(imminent)),
`ml.uncertain.checkin` (suspicion → MEDIUM). Pragmatics gate blocks ML raising when rules
discounted on positive evidence (negation/figurative).

Escalation: 5-row policy table (NONE/LOW/MEDIUM/HIGH/IMMINENT) → EscalationPlan with
pre-written localized message (i18n JSON, only {emergency_number} placeholder) + helplines
(helplines.json, 34 resources, validated by schema, last_verified per entry).

## LLM provider layer (Day 10)

Interface `LLMProvider` with `complete` + `stream` (stream declared without async so unconfigured
fails eagerly). Typed errors drive retry: retryable flag, not exception names.

```
Retry: exponential backoff with full jitter, total budget across attempts
Breaker: failure threshold → open → cooldown → half-open probe
Chain: primary (anthropic) → ollama (local) → canned (terminal, cannot fail)
```

Redaction (emails, phones, IDs, cards Luhn-checked, URLs) runs inside chain before any provider
sees bytes. System prompt is versioned, hashed, load-time validated file (10 rules from brief).

## Chat orchestrator — the heart (Day 11)

### Pipeline per user message

```
                         ┌─────────────────────────────────┐
                         │  POST /chat/sessions/{id}/msg   │
                         └──────────────┬──────────────────┘
                                        │
                     1. Validate ───────┘  length (chat_max_message_chars),
                        │                 encoding (no \x00, utf-8),
                        │                 rate limit per user (20/min)
                        │
                     2. Safety ──────────┘  RuleEngine.assess(text)
                        │                   MLClassifier.predict(text)
                        │                   ensemble.combine → final RiskAssessment
                        │                   Escalator.plan → EscalationPlan
                        │                   if HIGH/IMMINENT:
                        │                     → STOP, crisis reply, no LLM call
                        │                     → persist encrypted + SafetyEvent
                        │                     → response_type="crisis"
                        │
                     3. PII redaction ────┘  Redactor.redact(original)
                        │                   text for LLM = [EMAIL]/[PHONE]/etc
                        │                   original kept for emotion + storage
                        │
                     4. Emotion ──────────┘  EmotionAnalyzer.analyze(original)
                        │                   → EmotionResult.primary / valence
                        │
                     5. Retrieval stub ───┘  retrieve(redacted, session_id) → []
                        │                   Day 13: user journal/mood when consented
                        │
                     6. Prompt build ─────┘  system_v1.md (hashed, versioned)
                        │                   + conversation window (last N turns)
                        │                   + emotion hint ("User seems sad...")
                        │                   + safety hint ("MEDIUM risk: gentle check-in")
                        │                   + retrieval context (empty today)
                        │
                     7. LLM call ─────────┘  LLMChain.complete(messages, system=prompt)
                        │                   degraded flag if fallback used
                        │
                     8. Output guard stub ┘  guard_output(text) → pass-through today
                        │                   Day 14: diagnosis/medication/method checks
                        │
                     9. Persist & return ─┘  ChatRepository.add_message (encrypted)
                                            SafetyEvent when policy.record_event
                                            return {reply, risk_level, emotion,
                                                    response_type, resources, degraded}
```

### Detailed flow

```
User ──> API ──> Orchestrator.handle_message()
                  │
                  ├─> ValidationError? → 400
                  ├─> RateLimited? → 429 + Retry-After
                  │
                  ├─> Safety:
                  │     rules ──┐
                  │             ├─> combine() ──> final Assessment ──> EscalationPlan
                  │     ML ─────┘
                  │     if is_crisis:
                  │         return crisis template + helplines (no LLM)
                  │
                  ├─> Emotion (original text)
                  ├─> Redaction (for LLM)
                  ├─> Retrieval (stub [])
                  ├─> Prompt: system + window + hints + retrieval
                  ├─> LLMChain (with its own redaction + token guard)
                  ├─> Output guard (stub)
                  └─> Persist + return metadata
```

### Session types

- **Persistent**: requires `store_chat` consent, stored encrypted in `chat_sessions`/`messages`,
  survives restart, listed via DB.
- **Ephemeral**: default when `store=False` or no `store_chat` consent, lives in
  `EphemeralStore` (in-memory dict, TTL 30 min, RLock), no DB rows, purged on expiry.
  Verification checks: `SELECT COUNT(*) FROM messages WHERE session_id = :ephemeral_id` = 0.

Ownership: every session lookup checks `session.user_id == current_user.id`, else 403/404.
User A cannot read User B's session (tested).

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/chat/sessions | Create session, `{"store": bool}`; ephemeral by default |
| GET | /api/v1/chat/sessions | List own sessions (persistent + ephemeral merged) |
| GET | /api/v1/chat/sessions/{id} | Get one session, ownership enforced |
| GET | /api/v1/chat/sessions/{id}/messages | List messages (decrypted) |
| POST | /api/v1/chat/sessions/{id}/messages | Send message, JSON response with metadata |
| GET | /api/v1/chat/sessions/{id}/messages/stream?content=... | SSE stream (token events + final metadata) |
| POST | /api/v1/chat/sessions/{id}/messages/stream | SSE stream with JSON body |

SSE format:

```
event: token
data: {"token": "Hello"}

event: token
data: {"token": " world"}

event: done
data: {"risk_level": "none", "emotion": "joy", "response_type": "normal", "resources": [], "degraded": false}

data: {"type": "final", "risk_level": "none", "emotion": "joy", "response_type": "normal", "resources": []}
```

Final event carries `{risk_level, emotion, response_type, resources}` so UI can show CrisisCard.
Resources contain `HttpUrl` fields — `model_dump(mode="json")` + `jsonable_encoder` before `json.dumps`
is required, otherwise `TypeError: Object of type HttpUrl is not JSON serializable` truncates the stream
after token events (fixed in Day 11 verification).

### Fallback

LLM chain ends in `CannedProvider` which cannot fail. If primary + ollama both fail,
`degraded=True, fallbacks_used>0, provider="canned"` and reply is the canned supportive
text. Endpoint returns 200, not 500. Verified by killing primary in tests.

### Privacy

- No raw text at INFO: logs carry `text_sha` (16-hex) + length + metadata only
- PII redaction before LLM: verified by Fake provider capturing payload
- Encrypted at rest: `content_encrypted` is Fernet token, no plaintext column
- Ephemeral leaves no rows: verified by DB count queries
- Safety events: metadata only (level, source, user/session ids), no text

## Frontend (Day 5)

- Vite + React 18 + TS strict + Tailwind + React Router + TanStack Query
- Design tokens as CSS vars, two palettes (light/dark) via `data-theme`
- Accessibility: skip link, landmarks, focus trap in dialogs, contrast ≥4.5:1 tested arithmetically,
  reduced-motion honoured
- API client: single-flight refresh, one replay, proactive refresh
- Crisis button on every route, modal with helplines from backend (public endpoint)

## CI & Docker (Day 7)

- GitHub Actions: backend (ruff, format, mypy strict, pytest cov 80%), frontend (eslint, tsc, vitest, build),
  secrets-scan (gitleaks blocking), dependency-audit (report-only)
- Images: api (multi-stage, non-root app, healthcheck /health, no nlp extra, compose EMOTION_ANALYZER=keyword),
  web (nginx, hashed assets, SPA fallback, /api proxy)
- Compose: api + web + postgres:16-alpine, dev defaults (zero-bytes Fernet key), auto-migrate only in dev

## Evals

- `evals/datasets/` — 572 synthetic crisis cases (en/hi/hi-Latn/bn), hard families, no method words,
  split train 340/dev 116/test 116 (frozen, hashed)
- `evals/run_safety_eval.py` — precision/recall/F1 per level, binary HIGH+IMMINENT, per-language,
  confusion matrix, false negatives listed
- Day 9: HIGH+IMMINENT recall 1.000 on frozen test (51 cases), precision 0.567, 27 benign crisis cards
- Day 10: LLM redaction properties, no hard-coded model id, prompt rules checked

## ADRs

- 0001 Monorepo and stack
- 0002 Backend skeleton conventions
- 0003 Data layer, encryption, repos
- 0004 Auth, anonymous mode, consent
- 0005 Frontend skeleton, tokens, a11y floor
- 0006 Emotion model choice and mapping
- 0007 CI, Docker, dev stack
- 0008 Crisis detection rules engine and helplines
- 0009 Safety ML ensemble (raise-only)
- 0010 LLM abstraction (chain that cannot fail)
- 0011 Chat orchestrator (this doc) — pending

## Parking lot (future)

See PROGRESS.md parking lot. Day 11 adds: Redis for rate limit + ephemeral store,
output guard (Day 14), retrieval (Day 13), per-provider telemetry, streaming wired to UI.
