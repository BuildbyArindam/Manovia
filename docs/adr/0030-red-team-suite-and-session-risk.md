# ADR 0030: Red-team suite, session risk accumulator, stored-channel injection tripwire, family-bound access tokens

- Status: accepted (Day 29)
- Date: 2026-10-13
- Deciders: Manovia maintainers
- Supersedes: none. Extends [0008 crisis rules engine](0008-crisis-detection-rules-engine.md), [0009 ML ensemble](0009-safety-ml-ensemble.md), [0014 output guard and memory](0014-output-guard-and-conversation-memory.md) and [0004 authentication](0004-authentication-and-consent.md) (refresh-token families)
- Related: [0024 evaluation harness](0024-evaluation-harness.md), [0029 branch coverage gates](0029-branch-coverage-gates.md), [red-team report](../redteam-report.md)

## Context

Day 29 ran a scripted red team (`evals/redteam/`) against the real HTTP API.
Before any fix, 58 of 140 cases failed. Four of the failures needed a design
decision rather than a new rule:

1. **Gradual disclosure.** Over 6–8 messages, a person can say nothing that
   is a crisis on its own while the conversation as a whole is one
   (RT-S-MT-01/02). The per-message engine judged every message correctly and
   never escalated.
2. **Injection through stored channels.** A sentence in a chat message could
   end up in the rolling memory, and a sentence in a retrieved passage could
   end up in the system prompt. Either one is then repeated to the model on
   every later turn (RT-I-MEM-01, RT-I-RAG-01, RT-I-KB-01).
3. **Access tokens outlived their session.** After logout, or after a detected
   refresh-token reuse burned the family, the access token kept working for
   its remaining ≤15 minutes. A stolen access token survived the very event
   meant to stop the thief (RT-P-TK-02/03).
4. **Abuse budgets.** Export (a full decrypt of a person's data) had no
   per-user limit. The liveness probe was rate limited, so a flood could make
   an orchestrator kill a healthy process. The limiter never forgot keys
   (RT-P-EX-02, RT-D-14, RT-D-15).

## Decision

### 1. Session risk accumulator (`app/services/safety/session_risk.py`)

- After the per-message assessment (rules, then the ML ensemble when it is
  on), the chat orchestrator applies a deterministic score over the last
  **8 user turns of the session**, for both saved and ephemeral sessions.
- Points: the current message scores LOW 1 and MEDIUM 2. Each stored user turn
  scores CAUTION 1 and ELEVATED 2.
- A score of **≥3 raises the level to MEDIUM** (a gentle check-in). **≥6 raises
  it to HIGH**, which gives the deterministic crisis response with the model
  not called.
- Only a current message that is LOW or MEDIUM on its own can trigger a raise.
  A neutral message is never escalated. A message about someone else, or
  inside a fiction frame, is never raised.
- The window **starts after the most recent crisis turn**: nobody is escalated
  twice for what they already said. A raise to MEDIUM happens at most once per
  window.
- The raise only ever increases the level. It is recorded as a rationale code
  (`session.accumulated_medium` / `session.accumulated_high`), never as text.
- The thresholds are hand-set. They are not learned and not tuned on the eval
  set.

### 2. Injection tripwire on stored channels (`app/services/safety/injection.py`)

- `looks_like_injection()` is a regex tripwire for override imperatives,
  chat-template role markers and persona switches.
- It is applied where text is **stored and replayed**: memory notes (an
  instruction-shaped sentence is dropped and the feeling kept, and older notes
  are cleaned on read), retrieved passages (the whole passage is dropped and
  the drop is logged as a code), and the knowledge-base loader (a document
  that addresses the model is an authoring error).
- It is **not** applied to the current user turn. The person's own words are
  what we answer, and filtering them would silently drop a cry for help that
  happens to say "ignore". `system_v3` (v2 plus one block) tells the model that
  pasted text, journal text and retrieved passages are data, not instructions.
  The output guard gains the `jailbreak` and `prompt_leak` categories (migration
  0010) to catch a model that obeys anyway.

### 3. Access tokens bound to their refresh family

- New access tokens carry a `fam` claim. `get_current_user` refuses a token
  whose family has no unrevoked refresh token, with 401 `token_revoked`.
- The check runs **after** the user lookup, so an unknown user still gets the
  same error as before and the error does not become an oracle.
- Expiry is not consulted. The newest refresh token of a family always
  outlives the access tokens minted with it.
- Tokens minted before Day 29 carry no family and are accepted until their
  ≤15-minute expiry. There is no forced logout on deploy.

### 4. Budgets

- `PRIVACY_EXPORT_PER_HOUR` (default 10) per user. Over budget returns 429
  `export_rate_limited` with Retry-After.
- `/api/v1/health` (static, no database) is exempt from the global per-IP
  limit. `/api/v1/ready` stays limited.
- The in-memory limiter sweeps stale keys once per window. Its size is exposed
  as a `tracked_keys` property and never as `__len__`, because a falsy empty
  limiter broke `limiter or default` wiring once already today.

## Consequences

- MT-01 escalates at turn 6 (check-in at turn 5). The 8-message benign control
  never escalates. After a crisis, a message that is MEDIUM on its own still
  gets its own check-in. That is close to nagging and needs a real-user review.
- The accumulator inherits the per-message engine's errors. With ML on, false
  MEDIUMs on benign text can add up. It never raises a NONE message, which
  limits the damage.
- The injection tripwire is easy to paraphrase past. It is a cheap first line
  for replayed text, not a defence of its own.
- Every authenticated request costs one more indexed query
  (`ix_refresh_tokens_family_id`). A *rotation* does not end the previous
  access token, because the family is still active.
- All budgets are per process. More than one worker multiplies them (this was
  already true, see the known issues).

## Verification

- Red-team suite: 82/140 at baseline, **150/150** final
  (`evals/reports/redteam/2026-10-13-{baseline,final}/summary.md`).
- Tests: `tests/safety/test_session_risk.py`,
  `tests/chat/test_session_accumulator.py`,
  `tests/redteam/test_suites.py::test_the_eight_message_script_escalates_at_turn_six`,
  `tests/chat/test_injection_channels.py`,
  `tests/integration/test_access_token_revocation.py`,
  `tests/integration/test_redteam_limits.py`.
- `make test`: backend 4158 passed, 3 skipped; safety modules meet the 95%
  branch gate.
- `make eval`: dev-split safety gate PASS with 0 regressions.
