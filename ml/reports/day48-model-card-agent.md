# Model card — Agentic navigator (Day 48)

| | |
| --- | --- |
| Card version | 1.0 (2026-10-20) |
| Status | candidate (deterministic fake evaluated; real LLM not in sandbox) |
| Config | `AGENT_*` in backend/app/core/config.py |
| Owner | Manovia maintainers |

## 1. What it is

Tool-using navigator over closed allow-list of 5 tools: search_directory, draft_message, make_appointment_prep, set_reminder (needs allow), explain_options. No browsing/payments/bookings.

## 2. Intended use

Find verified resources, draft a note you send yourself, prep for appointment, set a reminder you confirm. Always behind agent safety gate (reuses rules+ML+business logic).

## 3. Training data

No training by us. Directory is curated 27 entries (24 verified + 3 needs_verification excluded). Tool descriptions are code, not model weights.

## 4. Evaluation

40 scripted scenarios across 10 categories (evals/agent): 40/40 offline. Injection quarantine test included.

## 5. Limitations

- Without ANTHROPIC_API_KEY answers from fake (scripted); function-calling quality unmeasured.
- In-memory sessions; pending confirmations lost on restart.
- Directory curated not live; last_verified shown.

## 6. Safety

Agent safety gate reuses same engine as chat; at risk≥MEDIUM deterministic crisis flow answers, agent never invoked (0 model calls). Strict JSON schemas, max 6 steps + 4000 tokens, refused-instruction rule in system prompt.

## 7. Kill switch

`agent=false` → /agent routes 503 with help links. Agent never half-runs; fail closed.
