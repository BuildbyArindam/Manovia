# 0037 — Personalised intervention policy: contextual bandits behind a deterministic safety boundary

- Status: accepted for offline research; **not enabled in the product** (Day 37)
- Date: 2026-10-15
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0018 guided exercises](0018-guided-exercises.md), [0020 PHQ-9 and GAD-7](0020-phq-9-and-gad-7-screenings.md), [0021 daily plan](0021-daily-plan-and-week-3-review.md), [0028 clinical-review gate](0028-model-card-and-clinical-review-gate.md), [0031 ML platform](0031-ml-platform.md), [0036 mood forecasting](0036-mood-forecasting.md)

> **Numbering note:** the requested filename `0011-bandit-policy.md` is not available: ADR 0011 is the accepted chat-orchestrator safety-order record. ADR numbers are never reused (see `docs/adr/README.md`), so this decision takes the next free number, 0037.

## Context and problem framing

A daily-plan rule table can offer a useful deterministic suggestion, but it cannot learn which reviewed, optional exercise a particular person tends to rate more positively in a particular context. This decision adds a small contextual-bandit research implementation under `backend/app/services/plan/policy/`; it does not replace the existing plan rules or expose a new endpoint. No trained model or user-history posterior is served by the API in this milestone.

The policy is a **just-in-time suggestion**, not a treatment, diagnosis, crisis detector, or engagement system. It only ranks the fixed, reviewed allow-list:

- Eight existing Day-18 exercise definitions (`body-scan`, `box-breathing`, `emotion-labelling`, `grounding-54321`, `muscle-relaxation`, `slow-exhale-478`, `small-action-planner`, `worry-time`).
- Three existing daily-plan actions (`plan-check-in`, `plan-thought-record`, `plan-rest`).
- `support-options` is a deterministic safety response, not a learnable arm.

Adding a YAML exercise to the content catalogue does not grant it bandit eligibility; the explicit policy allow-list must be reviewed and changed separately.

### Context

Only bounded, structured features enter the learner:

- Local time-of-day bucket (morning, afternoon, evening).
- Current valence and energy ratings on the existing 1–5 scale, with explicit missingness.
- Closed-vocabulary emotion labels.
- Up to 14 recent curated exercise ids.
- Closed-vocabulary exercise preference tags, evaluated for the candidate action.

There is no field for message, journal, thought-record, assessment, or other free text. The user key is an internal state-store lookup key and is never part of an explanation or log event.

### Reward

For a started session with pre- and post-ratings, the reward is

`clip((post_rating - pre_rating) / 4 - abandonment_penalty, -1, 1)`

where the penalty is subtracted only when a started session is marked abandoned. The default penalty is `0.1`, with a hard maximum of `0.2`. A started abandoned session with no post-rating receives only the penalty; no rating is fabricated. A skipped or never-started suggestion returns **no observation** (`None`) and does not update a posterior. The rating is the person's own brief report, not a clinical outcome or a causal estimate.

The reward accepts no other signal. In particular it contains no time-in-app, session length, return frequency, streak, notification-open, or other engagement term.

### Assumptions and limits

1. A bounded pre/post rating difference is a usable, low-stakes preference signal. It is not evidence that an exercise caused the improvement; mood changes for many reasons.
2. The 1–5 scale has equal spacing for the normalization. A different scale requires a new reward contract and version.
3. A reward observed after a suggestion is attributable to the action actually rated. Production logging must distinguish a user choosing another option from rating the suggested action; this offline prototype does not persist or infer those records.
4. The small linear feature model is an interpretable baseline, not a full representation of context interactions. The simulator's four synthetic user groups are illustrative, hand-authored response surfaces, not a real population.
5. The empirical-Bayes population posterior warm-starts newly observed users. Before any population evidence exists, all arms have a neutral prior and deterministic catalogue-order tie-break; Thompson sampling is deliberately not random at that cold start. Population state is currently in memory and is neither durable nor cross-worker.
6. Logged propensities support one-step IPS/DR only when actions have overlap and the logged action is the action whose outcome is observed. Those assumptions can fail under non-random production assignment, overrides, non-stationarity, delayed ratings, or safety gating.

## Decision

### 1. Three algorithms, shared arm-specific Bayesian linear posteriors

`ContextualBandit` has one Bayesian linear posterior per user and arm plus an arm-specific shared population posterior. Each new user-arm posterior takes the current population mean as its prior mean and then updates from that user's own bounded reward. Population updates let later users start from accumulated cross-user evidence without making the prior a claim of universal benefit.

The supported algorithms are:

- **Epsilon-greedy:** posterior-mean action plus an epsilon-uniform mixture.
- **LinUCB:** posterior mean plus `alpha × posterior uncertainty`, with a logged epsilon-uniform mixture for known propensities.
- **Thompson sampling:** samples the arm's Bayesian linear predictive reward; its finite-draw randomized implementation reports the exact empirical action probabilities used for that decision.

All exact and estimated propensities are attached to the suggestion and logged without user identifiers or context. The safe wrapper recomputes the selected action's propensity if it suppresses an exploratory choice.

### 2. Safety constraints live outside the learner

`SafeInterventionService` gates before it calls `propose`:

- Risk `MEDIUM` or above **or any PHQ-9 item-9 response** returns deterministic `support-options`; no bandit call or propensity is made. The boolean means an item-9 response occurred, not only that the answer was positive.
- The candidate set is always the intersection with the explicit curated allow-list. Invalid learner output fails closed to a deterministic curated action.
- Exploration is capped per internal user at one suggestion in any rolling five decisions (at most 20%), with no consecutive exploratory suggestions. The learner cannot raise this cap.
- A suggestion includes other available curated options, and `can_choose_another` / `can_skip` are always true. Skips are not training failures.

The current implementation is a pure service seam. It is intentionally **not wired into `GET /plan/today`**: ADR 0021's transparent plan is unchanged, and current durable outcome storage, cross-worker policy state, clinical review and the product interaction for choosing/rating alternatives do not yet exist.

### 3. Explain from model features, not generated text

Each returned decision has a `Why this?` line based on the selected arm's largest positive linear feature contributions (time bucket, valence/energy, emotion label, preference match, recent exercise, or the baseline feature). Wording is associative, not causal, and always says the person can choose another option or skip. No LLM generates the explanation.

### 4. Offline evaluation and logging

`ml/simulation/jitai_sim.py` generates heterogeneous synthetic users in memory, compares a random offline reference with the three bounded policies, reports cumulative reward/regret and group-level reward, and runs IPS/DR on held-out uniformly logged data against simulator truth. Twenty independent seeds and confidence intervals are the default. Aggregate results and the synthetic event hash are logged to MLflow; no event-level file, dataset, or model weight is committed.

## Alternatives considered

- **Replace the current rule planner:** rejected. The rule planner is transparent and already handles PHQ-9 support; the bandit remains a separate, unserved research seam until durability, user choice and review are ready.
- **One population model only:** rejected because it cannot adapt to an individual's own post-exercise ratings.
- **Independent, uninitialized per-user models:** rejected because the first suggestion would be random or arbitrary. Shared empirical-Bayes warm starts and stable ties provide a non-random cold start.
- **Optimise retention, session duration or streaks:** rejected as manipulative and unrelated to the user's stated post-exercise rating.
- **Use a language model for action choice or explanations:** rejected; the allow-list, ratings and safety gate are deterministic and explainable without free-text generation.

## Consequences

- The policy is reviewable and testable offline, but it is **not production-ready**: in-memory state is lost on restart and is not shared across workers; no clinical or native-speaker copy review has occurred; the live app remains on deterministic daily-plan rules.
- The simulator's IPS/DR results establish only that the estimators behave on this synthetic, overlapping, one-step setup. They do not validate the policy for real users.
- Any product rollout requires a separate integration decision, durable privacy-reviewed per-user/population state, actual opt-in and override logging, a safety review, native-speaker review, and a monitored rollback plan.

## Verification

Backend property tests cover the MEDIUM+ and PHQ-9 gates, the rolling exploration cap, and the curated action allow-list. A mutation check disables the risk gate and confirms those tests fail. ML tests cover seeded reproducibility, regret reduction in the synthetic scenario, and IPS/DR agreement with simulator truth. Recorded run output is in [`docs/verification/day37-jitai.md`](../verification/day37-jitai.md) and `docs/evaluation-report.md`.
