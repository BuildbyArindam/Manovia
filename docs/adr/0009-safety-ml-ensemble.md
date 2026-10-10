# 0009 - A one-way ML backstop next to the rules engine

- Status: accepted (Day 9)
- Date: 2026-10-10
- Deciders: Manovia maintainers
- Supersedes: none
- Related: [0006 emotion model](0006-emotion-model.md), [0008 crisis detection rules engine](0008-crisis-detection-rules-engine.md)

## Context

ADR 0008 built a deterministic rules engine and argued — correctly — that a
probability is not something to put *between* a person in crisis and a helpline.
The rules engine is the gate and keeps the veto. Nothing in Day 9 changes that.

But 0008 also named its own ceiling: **recall is bounded by what somebody thought
to write down.** The out-of-table probe measured it: five invented high-risk
sentences, two caught. The misses were exactly the messages a rules vocabulary
cannot anticipate — indirect expressions with none of the obvious words
("I've been saying goodbye to people this week", "sab tay hai. aadhi raat ke
baad.", "চিঠিগুলো লিখে ফেলেছি"). A distressed person who phrases their crisis in a
way no maintainer happened to curate currently gets no detection at all.

Day 9's task is to add a statistical backstop for exactly that gap, without
giving back any of the guarantees 0008 won.

## Decisions

### 1. The ensemble is a ratchet: `final = max(rules, ML if confident)`

The ML classifier can **raise** an assessment. It can **never lower** one. This is
not a policy knob; it is the shape of the code (`ensemble.combine` returns only
branches that keep or raise the rules level) and it is pinned from the outside by
a hypothesis property test over arbitrary predictions and arbitrary thresholds.

Consequences:

- Every guarantee ADR 0008 bought is still true. If the rules say HIGH, the reply
  is the deterministic crisis card whatever the model thinks.
- The model's failure mode is restricted to over-escalation. Over-escalation shows
  somebody a warm message and a helpline; under-escalation can kill. We buy the
  recall of a statistical net and cap its damage at false alarms.

### 2. Three raise bands, strongest evidence first

`combine()` reads two numbers from the calibrated model — the top-class
confidence and `P(high)+P(imminent)` (the "crisis mass") — and applies:

1. **Confident top class** (`confidence >= safety_ml_min_confidence`): raise to
   the model's level. Rationale code `ml.raised`.
2. **Crisis mass** (`mass >= safety_ml_crisis_mass_floor`): the model is sure it
   is a crisis but torn between HIGH and IMMINENT, so the single top class
   undersells the evidence. Raise to HIGH. Rationale code `ml.crisis_mass`.
3. **Suspicion** (`mass >= safety_ml_suspicion_floor`, rules below MEDIUM): not
   confident enough for a crisis card — resolve to MEDIUM: respond normally,
   append the soft check-in and resources. Rationale code
   `ml.uncertain.checkin`. Uncertainty resolves toward noticing, never toward
   dismissing — and never to a crisis card on a hunch.

All three thresholds are environment settings (validated at startup), chosen by
grid search on the dev split only (`evals/tune_safety_thresholds.py`).

### 3. A pragmatics gate: ML may not undo the rules' discounts

When the rules engine matched risky words but discounted them on *positive*
evidence — a negation ("I *don't* want to die") or a plainly figurative frame
("this deadline is killing me") — the discount is evidence too. A statistical
raise over those same words would simply undo the pragmatics, which is how a
classifier would talk the system out of a correct NONE. So when
`negated_hits > 0` or `figurative_hits > 0`, bands 1 and 2 are blocked; the
gentle check-in band still applies.

### 4. Baseline first, behind an interface: TF-IDF + logistic regression

The classifier is `TfidfLogisticClassifier` — char-wb (2–4) + word (1–2) TF-IDF
and a multinomial logistic regression, sigmoid-calibrated (cv=3), trained offline
from the train split and committed as a joblib artifact with provenance metadata.
It sits behind `SafetyClassifier`, so a fine-tuned transformer can replace it
without touching the ensemble, the endpoint, or the tests.

Rejected alternatives:

- **A ZeroShot NLI model.** Would need a model download (network at startup,
  provider dependency in the safety path — the exact property 0008 rejected), and
  its zero-shot phrasing hypotheses would be our curation again, one level more
  indirect. Stays on the table for later, behind the same interface.
- **Fine-tuning a small transformer now.** The dataset is 572 synthetic cases.
  A transformer would overfit what it has and we would not know which numbers to
  believe. The baseline is the honest instrument for the data we have; the
  interface is the hedge.
- **Training at startup.** Non-reproducible, slow, and it would make every test
  run a model run. The artifact is committed; retraining is a deliberate act with
  a provenance diff.

### 5. The eval set is the contract: train/dev/frozen test

`evals/datasets/crisis_cases.jsonl` (572 synthetic cases: en, hi Devanagari,
hi-Latn, bn; five levels; hard negatives — figurative, news, media, third
person, recovery stories; hard positives — indirect, keyword-free) splits
deterministically 60/20/20. **The test split is frozen**: its SHA-256 is in
`manifest.json`, the eval runner refuses to score a drifted file, and a test
asserts that neither the training script nor the threshold tuner so much as names
the test file. Thresholds were chosen on dev; the first test-split look happens
at verification, in the PR, untouched.

No case names a method or a means: the builder asserts that against the
safe-messaging vocabulary extended with Indic means words. Synthetic crisis data
that described methods would be unsafe to train on and unsafe to evaluate on.

### 6. Audit rows move to the public endpoint

Day 8 kept `POST /crisis/assess` free of database writes because it is public.
Day 9 accepts the trade: at MEDIUM or above the endpoint writes a
`safety_events` row — stored tier plus which detector earned it (`rules`/`ml`),
never text — because an escalation audit trail is worth more than the small
metadata rows an attacker can add under the global rate limit. The row shape is
unchanged (metadata-only, enforced by model tests), and the authenticated chat
path will later write rows with full user/session ids.

## Consequences

- Recall-first by construction: the ML net catches phrasings the vocabulary
  missed, at a false-alarm cost that is reported honestly in
  `docs/safety-design.md` §12.6 (dev crisis recall 1.00 at the shipped
  thresholds; ~60% of benign dev cases escalated at this dataset size — the
  stated price of the recall target for a 572-case baseline, and the argument
  for more data before any transformer work).
- Every raise is auditable: `ensemble.source` + rationale codes +
  `EnsembleOut` on the response say exactly which band fired with what numbers.
- The safety package grew two modules and stayed private: no logging calls, no
  file writes, no cache keyable on a message (the one cache,
  `build_ml_classifier`, is keyword-only and keyed on an artifact path).
- Degradation is total: `SAFETY_ML_ENABLED=false`, a missing artifact, a missing
  scikit-learn, or any predict-time exception all produce Day 8 behaviour. The
  ML layer can be turned off without a deployment.
