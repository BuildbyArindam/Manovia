# ADR 0021 — Daily plan: a transparent rule table, not a recommender

Date: 2026-10-12 · Status: accepted

## Context

Day 21 adds the surface most likely to feel like pressure: the app telling a
person what to do today. Four risks shaped the design, and each is a place
where a plausible implementation is actively harmful:

1. **A suggestion nobody can audit is a suggestion nobody should trust.**
   "The model thinks you should breathe today" is not an explanation. In a
   wellbeing app, every nudge must answer "why this?" with a sentence a person
   can check against their own week.
2. **Nagging is a safety failure, not a UX wart.** Daily prompts for a person
   who is low and inactive can read as evidence of failure. Anything that
   counts missed days loudly, or fires on a single data point, turns support
   into surveillance.
3. **Assessment answers are clinical-adjacent data.** PHQ-9 item 9 is the
   suicidality item. Letting assessment data steer ordinary suggestions would
   make the screenings feel consequential in ways they are not designed to be.
   The only responsible influence is showing support options.
4. **Quiet hours are a promise.** If a person says "not after ten", a 23:30
   plan is a broken promise — except that a helpline is not a nudge, and
   safety outranks quiet.

## Decision

**A table of named rules, evaluated in priority order.** The whole planner is
`services/plan/daily_plan.py`: `RULES` is a tuple of `Rule(id, description,
build)` records, each a pure function from a `PlannerContext` dataclass to an
optional `PlanSuggestion`. No model, no LLM, no network, no learning. A
reviewer can read the rule table and the context in five minutes; a person can
read their suggestion's `why` and verify it against their own week.

**Hard limits, enforced by construction:**

- `MAX_SUGGESTIONS = 3`. The builder stops collecting at three; the frontend
  renders whatever arrives; there is no "and one more".
- Every `PlanSuggestion` requires a non-empty `title` *and* `why` — the
  dataclass rejects construction without both.
- At most one suggestion per kind per day (the rules dedupe by `PlanItemKind`).
- Rules fire on multi-day signals. A low mood needs at least three check-ins in
  the comparison window (or a mean ≤ 2.0 with enough data); the habit rule
  needs two active days; the missed-check-in rule waits two full days. One
  lonely data point never earns a nudge.
- Rest is an answer. When the rules find nothing and the person has been
  active, the plan says resting today counts too; otherwise the plan is simply
  empty. An empty plan is never an error state in the UI.

**PHQ-9 item 9 short-circuits everything.** A positive item-9 answer within
the lookback window collapses the plan to exactly one card: support options,
with every other suggestion set aside. The flag enters the context as a single
boolean (`phq9_item9_flagged`); no rule reads assessment data, so no future
rule can quietly start using it. The planner reads the answer through the
assessment's own metadata, never by re-scoring.

**Quiet hours suppress routine suggestions, not safety.** While the person's
quiet window covers "now" the plan is empty — with the documented exception
that the item-9 support card still shows, because a helpline is not a nudge.
Windows may cross midnight; a window whose bounds collapse covers the whole
day ("leave me alone" wins over "the setting silently switched off").

**The planner sees metadata only.** The API layer projects closed-vocabulary
columns — local dates, valence numbers, emotion tags, completion ids, the
"what helped" emptiness of a thought record — into `PlannerContext`. Encrypted
free text (mood notes, thought-record fields, chat) is never decrypted for
planning. Preferences (preferred exercise tags, quiet hours) are the only
settings the planner reads, stored in `user_settings` with strict `HH:MM`
validation matching the write path.

**Plans are idempotent per user and local day.** `GET /plan/today` builds the
plan the first time and persists `plan_items` (unique per user+date+kind);
later calls re-read. Done/skip are idempotent and skip needs no reason. The
day is the caller's local day via the same timezone seam the mood module uses;
an invalid timezone is the standard 422.

## Alternatives considered

- **An LLM planner.** Richer prose, and unauditable: the "why" would be a
  post-hoc justification, not a cause. Rejected by the transparency risk.
- **An ML recommender over exercise completions.** Would need training data
  Manovia does not have, and would optimise engagement — the wrong objective
  for a surface that must be allowed to say "nothing today".
- **Client-side planning.** Keeps the backend thin, but splits the rules
  across two codebases and makes "what does the app suggest and why" a
  frontend question. The rule table belongs next to the safety rules.

## Consequences

- Every suggestion is explainable by construction, and the explanations are
  asserted in 75 unit tests plus the eight-profile verification script
  (`scripts/verify_day21.py`).
- The rule table is the whole feature: new suggestions are new rows, reviewed
  like safety rules, not tuned like models.
- The planner cannot personalise beyond preferences and time of day. That is
  the point today; if a learned component is ever warranted, it must inherit
  the auditability and item-9 constraints this record fixes.
- Quiet hours are evaluated in the person's own zone; the API recomputes
  `quiet_hours_active` on read so a plan generated earlier does not nag after
  the window starts.
