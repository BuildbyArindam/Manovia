# Architecture

How a message travels through Manovia. Decisions and their reasons live in the
[ADRs](adr/); this page is the map. Day 11 added the chat orchestrator
([ADR 0011](adr/0011-chat-orchestrator.md)), which is the first place the
earlier layers run in sequence.

## System

```mermaid
flowchart LR
    UI["Frontend (React/Vite)"] -->|"/api/v1"| API["FastAPI app"]
    API --> AUTH["Auth + consent gates (Day 4)"]
    API --> CHAT["Chat endpoints (Day 11)"]
    CHAT --> ORCH["ChatOrchestrator"]
    ORCH --> SAFETY["Safety: rules + ML ensemble (Days 8-9)"]
    ORCH --> NLP["Redactor + emotion (Days 6, 10)"]
    ORCH --> LLM["LLM provider chain (Day 10)"]
    ORCH --> DB[("DB: encrypted messages, safety events")]
    ORCH --> MEM[("Process memory: ephemeral sessions")]
    LLM --> FAKE["fake / anthropic / ollama / canned"]
```

## The chat pipeline

One user message, in this exact order. **The order is the safety property**:
nothing that can call a model runs before the crisis gate, and nothing leaves
the process before redaction.

```mermaid
flowchart TD
    A["POST /chat/sessions/{id}/messages<br/>or .../stream (SSE)"] --> B["0. Auth, consent (ai_disclosure + terms),<br/>session ownership (404 if not yours)"]
    B --> C["1. Validate length / encoding<br/>+ per-user rate limit (429 + Retry-After)"]
    C --> D["2. Input safety<br/>rules engine + ML ensemble -> RiskAssessment"]
    D -->|"engine error"| X["503 safety_unavailable<br/>(fail closed, no LLM)"]
    D -->|"HIGH / IMMINENT"| K["Deterministic Day 8 crisis reply<br/>NO LLM CALL<br/>persist user msg (encrypted) + SafetyEvent<br/>response_type = crisis"]
    D -->|"NONE / LOW / MEDIUM"| E["3. Redact PII from the text bound for the LLM<br/>(history is redacted too)"]
    E --> F["4. Emotion analysis (original text)"]
    F --> G["5. Retrieval (stub -> [] until Day 13)"]
    G --> H["6. Build prompt<br/>system + short window + emotion hint<br/>+ safety hint (MEDIUM: gentle check-in) + retrieval"]
    H --> I["7. LLM via provider chain<br/>(error / outage -> fallback template)"]
    I --> J["8. Output guard (stub, pass-through until Day 14)"]
    J --> L["9. Persist assistant message (encrypted)<br/>MEDIUM: append check-in, return helplines"]
    K --> R["Response: reply + metadata<br/>{risk_level, emotion, response_type, resources, crisis, check_in}"]
    L --> R
```

### What each step may and may not do

| # | Step | On failure | Notes |
| --- | --- | --- | --- |
| 1 | Validate, rate limit | 422 / 429 | Before any work; blank, over-long and control-character input is refused with a curated message. |
| 2 | Input safety | **503, no LLM** | HIGH/IMMINENT end the pipeline here. MEDIUM continues with a hint and an appended check-in. |
| 3 | Redaction | No model call, canned fallback | Idempotent; the provider chain redacts again. |
| 4 | Emotion | `emotion = None` | Reads the *original* text. |
| 5 | Retrieval | `[]` | Stub until Day 13. |
| 6 | Prompt | n/a | Hints go in the system prompt; user id never included. |
| 7 | LLM | Fallback template | `response_type = fallback`, `degraded = true`; never a 500. |
| 8 | Output guard | Canned reply | Stub until Day 14; `requires_full_text` makes streams buffer-then-release. |
| 9 | Persist | Logged, reply still returned | `persisted = false`. |

## Where a conversation lives

| | Ephemeral (default) | Saved (`save_history = true`) |
| --- | --- | --- |
| Needs consent | `ai_disclosure`, `terms` | + `store_chat` |
| Stored in | Process memory, 30-minute sliding TTL | `chat_sessions` + `messages` (Fernet-encrypted text) |
| After idle / restart | Gone | Kept |
| MEDIUM+ turn | Anonymous `SafetyEvent` (no user, no session, no text) | `SafetyEvent` linked to the session |

## Response contract

JSON: `{reply, session_id, message_id, metadata}`.
SSE: zero or more `event: token` (`{"text"}`), then one `event: final` with the
same fields plus `replaced`; `final.reply` is authoritative.

`metadata.response_type` precedence: `crisis` > `fallback` > `check_in` >
`normal`. `metadata.crisis` carries what the frontend `CrisisCard` needs
(message, helplines, emergency instruction). Helpline data comes only from
`backend/app/content/helplines.json`.

## Not here yet

Retrieval (Day 13), the output guard (Day 14) and the chat UI. Their seams
(`Retriever`, `OutputGuard`, `ChatPage.tsx`) exist and are tested as stubs.
