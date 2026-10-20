# Manovia v2.0 — Technical Report

**A privacy-first, safety-first self-help companion: design, safety architecture,
models, and an honest evaluation of what was measured**

Version 2.0.0 · 20 October 2026 · Repository: `BuildbyArindam/Manovia` (see
[README](../README.md)) · Branch of record for this report:
`day-49-v2-0-integration-technical`

> **Status.** Portfolio project. Not a clinical service, not a medical device, not
> a crisis service. No licensed clinician has reviewed the copy, the helplines or
> the instruments' presentation, and no native speaker has signed off the Hindi
> and Bengali translations ([clinical review checklist](clinical-review.md),
> [translation status](../translations/STATUS.md)).

---

## Abstract

Manovia is a chat-first self-help companion for mental wellbeing, aimed at
English-, Hindi- and Bengali-speaking users in India. It is built around one
non-negotiable ordering: **deterministic crisis rules decide risk before any
language model is called**, a high-risk message receives a pre-written,
localised card with helplines and no model output at all, and every model reply
passes a deterministic output guard before it is stored or shown. Around that
core sit mood check-ins, an encrypted journal, guided exercises, a CBT thought
record, PHQ-9/GAD-7 screenings with published scoring, an opt-in numeric mood
outlook, an on-device "Private Mode", a consented clinician-share bridge, and a
research ML platform whose results are gated before they can reach a user.

We report what was measured and what was not. On a 116-case synthetic
development split (51 high or imminent), the rules-plus-ML ensemble reaches
**recall 1.000 (95 % CI 1.000–1.000) at precision 0.543**; the rules alone reach
**recall 0.333**. The nine-emotion multilingual analyser reaches **macro-F1
0.804** over 162 cases, collapsing to **0.011** under deliberate domain shift.
The retrieval change from v1.0 reaches **MRR 0.658 / Recall@1 0.354** on a frozen
52-query split for **+5.5 ms** of p95 latency. The agentic navigator passes
**40/40** scripted scenarios, including confirmation gating and prompt
injection. The fairness audit passes five models over eleven identity dimensions
with a largest gap of **0.026** against a 0.10 ceiling. Three research
directions — sequence-model mood forecasting, federated learning with
differential privacy, and a contextual-bandit intervention policy — were
implemented, run and **not shipped**, because the measurements did not support
shipping them; the neural forecaster, in particular, **lost to a seven-day moving
average** (MAE 0.561 vs 0.554) on held-out synthetic users.

---

## 1. Introduction

Self-help software for mental wellbeing fails in two characteristic ways. It
either over-claims — presenting a chat interface as a substitute for care — or it
under-delivers on safety, leaving the hardest moments (suicidal ideation, acute
distress) to a generative model that was never designed to handle them. Manovia
was built as a small, deliberate answer to both: a product whose safety path is
older than its AI, and a project whose claims are bounded by committed
measurements.

**Scope.** The product is a self-help companion: supportive conversation,
structured self-help tools, and a way to bring a summary to an appointment. It
does not diagnose, does not discuss medication doses, does not promise outcomes,
and does not pretend to be a person.

**Contributions of v2.0**, relative to the v1.0 release (Day 30):

1. An **ML platform** (`ml/`) with a licensed dataset catalogue, DVC-versioned
   splits, config-validated reproducible training, MLflow tracking, release gates
   (reproducibility, size/quantisation, fairness, DP budget) and committed model
   and dataset cards.
2. **Safety work that never leaves the rules in charge**: a calibrated ML
   backstop that may only raise caution ([ADR 0009](adr/0009-safety-ml-ensemble.md)),
   an in-browser re-implementation of the same safety engine with a 500-phrase
   parity gate ([ADR 0040](adr/0040-in-browser-private-ai.md)).
3. **Tools that need care to be safe**: real-time voice with barge-in
   ([ADR 0039](adr/0039-realtime-voice-companion.md)), HRV-paced breathing
   ([ADR 0041](adr/0041-biofeedback-hrv.md)), a WebXR calm space with a 2D
   fallback ([ADR 0046](adr/0046-xr-calm-space-and-sensory-profile.md)), an
   agentic care navigator restricted to read-only tools
   ([ADR 0043](adr/0043-agentic-care-navigator.md), [ADR 0044](adr/0044-directory-mcp-server.md)),
   and a clinician-share bridge whose decryption key the server never holds
   ([ADR 0047](adr/0047-clinician-share-bridge.md)).
4. **A responsible-AI layer**: a 21-item risk register with gates
   ([risk register](ai-risk-register.md)), privacy-only drift monitoring,
   twelve kill switches, a user-facing transparency page, and a red-team suite
   with regression tests ([Day 48 red team](verification/day48-redteam.md)).

Everything in this report is traceable to a file in the repository, listed in
§9. Where a claim cannot be traced, it is not made.

---

## 2. System design

Manovia is a monorepo: a FastAPI backend (Python 3.11+, Pydantic v2, SQLAlchemy 2,
Alembic), a React + Vite + TypeScript frontend with a service worker, PostgreSQL
in production (SQLite for local work), and a separate `ml/` project. The
architecture poster is [docs/assets/v2-architecture-poster.svg](assets/v2-architecture-poster.svg);
the diagram-level description is [docs/architecture.md](architecture.md).

### 2.1 The request path

A chat turn is processed in a fixed order, and **the order is the safety
property** ([ADR 0011](adr/0011-chat-orchestrator.md)):

1. authentication, consent gate, owner-only session lookup;
2. validation and per-user rate limiting;
3. **crisis assessment** — normalisation, deterministic rules, then the optional
   ML backstop, which may raise the level and never lower it;
4. a high-risk turn **stops here**: a pre-written localised card, helplines and
   an emergency instruction are returned, the model is never called;
5. otherwise: PII redaction, emotion analysis, curated retrieval
   (BM25 + dense with reciprocal-rank fusion, optional cross-encoder rerank, and
   a calibrated "no good answer" floor), and the provider chain
   (Anthropic → Ollama → canned supportive reply);
6. the **output guard** — deterministic checks for diagnosis, medication,
   promises, prompt leakage and tone — accepts, replaces or regenerates the
   reply, for the whole reply and before any token is streamed;
7. storage, with sensitive fields encrypted at rest.

### 2.2 Two execution modes

**Standard mode** is the path above. **Private Mode** ([ADR 0040](adr/0040-in-browser-private-ai.md))
runs the same normalisation, rules, escalation and output guard *compiled from the
same content* to JavaScript, in the browser, with **no network** for the
conversation; replies come from a local model if one has been pinned and
consented to, otherwise from reviewed templates. Messages are stored as AES-GCM
ciphertext in IndexedDB under a key derived from a passphrase the server never
sees. The two implementations are kept honest by a parity gate: 500 phrases,
identical risk levels, 0 mismatches; plus 1,671 casefolded code points whose
Python/JavaScript disagreements are pinned as data (§5.6).

### 2.3 Availability posture

Offline, the service worker answers navigations and the app shell from precache,
serves "Need help now" from the last-fetched helplines, and queues mood check-ins
in a local outbox that replays with a client-generated id
([ADR 0025](adr/0025-pwa-accessibility-low-bandwidth.md)). Heavy modules —
WebXR, WebLLM/transformers.js, charts, the patterns view — are **lazy route
chunks** (§5.8), so a first visit costs 133.1 KB gzip of JavaScript, not the sum
of everything the project can do.

---

## 3. Safety architecture

### 3.1 Ordering and authority

The safety rules in [AGENTS.md](../AGENTS.md) are the product's constitution:
crisis detection runs before any LLM call; the model never improvises at high
risk; nothing diagnoses, prescribes or promises; AI nature is disclosed; every
output passes a deterministic check; raw text is never logged at INFO or above;
helplines live in one file with a `last_verified` date.

Authority is one-directional. The rules engine ([ADR 0008](adr/0008-crisis-detection-rules-engine.md))
holds the decision. The ML backstop ([ADR 0009](adr/0009-safety-ml-ensemble.md))
may raise a level it is more worried about; it cannot lower one, cannot write the
crisis card, and never receives identifiers. This is why there is no "model
decides crisis" path anywhere in the codebase, and why the evaluator asserts the
rules-only recall floor separately (0.333 on dev) so that rule rot cannot hide
behind a strong model.

### 3.2 What a person sees at high risk

A high or imminent assessment returns a reviewed card in the user's language:
validation, an explicit "I am not a person and not the right support for this",
helpline intro and lines, an emergency instruction (112 in India, 911/999/000
elsewhere), a "tell one person you trust" step, short safety steps, a closing
line and a disclaimer. The rendering path is deterministic; no model text can
appear inside it. The chat request is persisted as a safety event, with the risk
level and categories, and without the message text in logs.

### 3.3 The price of recall-first

The ensemble is tuned for recall. On the 116-case dev split it escalates 31 of
51 benign cases (precision 0.543) — mostly past-tense recovery narratives,
third-person worries and media references that share vocabulary. The Day-49
journey reproduced the everyday edge of this: the sentence *"I had a long day and
I am tired."* was escalated to **high** by the ML backstop (`ml.crisis_mass`
0.372, floor 0.30) and received the crisis card. That is a false alarm rather
than a missed crisis, and it is the direction this project chose — but it is a
real cost, it is visible in the product, and §7 treats it as the most important
open calibration problem.

### 3.4 Defence in depth

| Layer | Mechanism | Artefact |
| --- | --- | --- |
| Input ordering | rules before any model call | `backend/app/services/safety/pipeline.py` |
| Model restraint | raise-only contract, tested | `backend/tests/safety/`, [ADR 0009](adr/0009-safety-ml-ensemble.md) |
| Output | whole-reply deterministic guard, per language | [ADR 0014](adr/0014-output-guard-and-conversation-memory.md) |
| Session risk | repeated medium signals raise the floor | [ADR 0030](adr/0030-red-team-suite-and-session-risk.md) |
| Injection | prompt-injection screening in text and (Day 48) in spoken input | `docs/verification/day48-redteam.md` |
| Tool use | read-only tools; effecting tools require explicit confirmation with a uuid4 token and 15-minute TTL | [ADR 0043](adr/0043-agentic-care-navigator.md) |
| Operation | 12 kill switches, in-memory, audited, no restart | `backend/app/services/feature_flags/` |
| Monitoring | privacy-only drift aggregates; PSI/KS alerts | `backend/app/services/drift/monitor.py` |
| Governance | 21-item risk register with gates | [risk register](ai-risk-register.md) |

The red-team suite is 150 scripted attacks from Day 29 (final run 150/150) plus
12 feature-specific findings from Day 48, each with a regression test. Attacks
that succeeded were fixed rather than documented as acceptable, and one fix
(agent confirmation tokens) replaced a sequential identifier with a uuid4.

---

## 4. Models and data

### 4.1 What is actually served

| Component | Served | What it is | Where |
| --- | --- | --- | --- |
| Crisis rules | yes | deterministic patterns + normalisation, en/hi/hi-Latn/bn | `backend/app/content/safety/` |
| ML backstop | yes (default) | calibrated TF-IDF + logistic regression, 5 levels | `app/services/safety/ml_classifier.py` |
| Emotion | yes | lexicon chain, optional HF checkpoint | `app/services/nlp/` |
| Retrieval | yes | hybrid BM25 + dense + cross-encoder, calibrated abstain | `app/services/rag/` |
| LLM | yes | Anthropic / Ollama / canned fallback | `app/services/llm/` |
| Private-mode safety | yes | the same rules compiled to JS | `frontend/src/features/private/` |
| On-device model | **no** (unpinned) | WebLLM / transformers.js adapter, template fallback | [ADR 0040](adr/0040-in-browser-private-ai.md) |
| Mood sequence models | **no** | GRU/LSTM/Transformer research | `ml/src/manovia_ml/mood_forecast/` |
| Federated + DP | **no** | FedAvg/FedProx + Opacus research | `ml/src/manovia_ml/federated/` |
| Bandit policy | **no** | LinUCB / Thompson research | `ml/src/manovia_ml/simulation/` |

### 4.2 Data

All data in the repository is synthetic or hand-written. The dataset catalogue
(`ml/data/CATALOGUE.md`) records 17 datasets with source URL, licence, allowed
use, languages, size, label scheme, known biases and personal-data status; ten
are marked restricted, and "DO NOT USE" is a verdict the pipeline enforces rather
than a comment. Evaluations use three hand-authored sets: 572 crisis cases (340
train / 116 dev / 116 frozen test, en + hi Devanagari + hi-Latn + bn), 162 emotion
cases across nine emotions with an explicit domain-shift stratum, and 60 response
scenarios. Training data for research runs is either the toy fixture (80 rows,
committed) or DVC-generated splits that are **never committed** (AGENTS.md
ML ground rules 3–4).

No real user text exists anywhere in this repository. That is a limitation as
much as a virtue: it means every number here describes synthetic text written by
the author, and §7 says what that costs.

### 4.3 Reproducibility

Each run logs the git commit (with a dirty flag), dataset hashes, resolved
config, seed, library versions, metrics and artefacts to a local MLflow file
store; the same config and seed must reproduce metrics within tolerance, and CI
checks committed baseline expectations ([ADR 0031](adr/0031-ml-platform.md)).

Two reproducibility defects were found and fixed during the v2.0 verification,
both invisible on the day they were written:

- **A wall-clock date and an absolute path in a DVC output.** `manifest.json`
  carried `"built": <today>` and resolved seed paths, so `dvc.lock` moved every
  calendar day and every checkout directory, and `dvc status` could never be
  clean. Manifests are now relative and date-free, with a byte-stability test.
- **Salted `hash()` in the fairness stand-in predictors.** Python salts `hash()`
  on `str` per process, so `ml/reports/fairness.json` changed between identical
  runs. The predictors now seed from SHA-256, and a test runs the gate script in
  two processes with different `PYTHONHASHSEED` values and compares bytes.

A third defect was found in the fairness gate itself: `max_gap_code_mixing` never
matched the `code-mixing` category, so that dimension silently ran against the
global 0.15 ceiling instead of its configured 0.12. Category names are now
normalised, and a test asserts that **every** template category has a configured
ceiling — the failure mode of a silent mismatch is always permissive.

---

## 5. Evaluation

### 5.1 Method

Safety and emotion numbers are point estimates with **95 % percentile bootstrap
intervals (B = 2000, seed 20261012)** over small synthetic splits; the report
gives intervals rather than bare numbers because a 116-case split does not
support two decimal places of confidence. Safety has four independent CI gates:
ensemble recall ≥ 0.970, rules-only recall ≥ 0.330, per-language recall ≥ 0.950,
and zero recall regression against the previous dated report
([evaluation report](evaluation-report.md)). The gates are the product decision;
the intervals are the honesty.

### 5.2 Safety detection

| Condition | Recall (95 % CI) | Precision (95 % CI) | Gate |
| --- | --- | --- | --- |
| Ensemble, HIGH+IMMINENT, dev (n = 116, 51 crisis) | **1.000** [1.000, 1.000] | **0.543** [0.441, 0.645] | ≥ 0.970 → PASS |
| Rules only, HIGH+IMMINENT, dev | **0.333** [0.204, 0.471] | 0.810 | ≥ 0.330 → PASS |
| Ensemble, frozen test split (Day 24) | 1.000 (51/51, exact 95 % lower bound 0.930) | 0.567 | informational |

The test split has been scored at least once, so its figures are not a clean
held-out result. Exact `IMMINENT` recall is 0.100 (2/20) while combined
HIGH+IMMINENT recall is 1.000 — the card and the helplines are the same for both
levels, so the practical consequence is a softer template, not a missed crisis;
it is stated here because "we catch every imminent case" would be false.
Artefacts: [safety.md](../evals/reports/2026-10-20/safety.md),
[safety-design.md](safety-design.md).

### 5.3 Emotion and responses

| Task | Metric | Value (95 % CI) | Notes |
| --- | --- | --- | --- |
| 9-emotion, 162 cases | macro-F1 | **0.804** [0.739, 0.853] | accuracy 0.765 |
| in-domain (123) | macro-F1 | 0.992 | explicit affective vocabulary |
| domain shift (39) | macro-F1 | **0.011** | somatic descriptions, sarcasm, unseen script tokens |
| 60 response scenarios | pass rate | **1.000** (60/60) | 6 deterministic checks, FakeLLMProvider |

The domain-shift collapse is the most useful number in this table: the shipped
lexicon chain is close to useless on text that describes a feeling somatically
("my jaw is clenched so tight"), which is exactly how many people describe
distress. This is why the emotion model is optional and why nothing in the safety
path depends on it.

### 5.4 Retrieval (v2.0 change)

Frozen 52-query test split, k ∈ {1,3,5}, eight settings compared on the same
harness ([ADR 0035](adr/0035-fine-tuned-retrieval.md)):

| Setting | Recall@1 | MRR | p95 latency |
| --- | ---: | ---: | ---: |
| v1.0 baseline (dense only, off-the-shelf) | 0.292 | 0.639 | 3.0 ms |
| **`hybrid_rerank` (shipped)** | **0.354** | **0.658** | **8.5 ms** |
| `hybrid_finetuned_rerank` (measured, not shipped) | 0.338 | 0.707 | 8.5 ms |

The higher-MRR fine-tuned setting is **not** shipped: it has no calibrated
abstention threshold, so it cannot say "I have no good answer", and a retrieval
system that always answers is a safety problem in this product. The shipped
setting's abstain precision is 0.786 and abstain recall 0.833. The final
recommendation comes with its own caveat: no setting reached the configured 0.90
answer-precision bar on the smoke split, so the choice is "best available", not
"clears the bar" ([day35-retrieval.md](../ml/reports/day35-retrieval.md)).

### 5.5 Forecasting, policy and federated research

| Experiment | Headline | Verdict |
| --- | --- | --- |
| Mood forecasting (72 synthetic users, 210 days, held-out 15 users) | best baseline `moving_average_7d` MAE **0.554**, MASE 0.915, coverage 78.3 % / 80 % nominal; best neural `transformer` MAE **0.561**, MASE 1.024 | **not shipped** — the neural model did not beat the baseline |
| Contextual bandit (20 seeds, 108,800 synthetic interactions) | LinUCB cumulative reward **193.795** [186.693, 200.897] vs random −73.388; exploration capped at 20 %, never consecutive | **not shipped** — needs ~60 decisions/user, and learning on someone's worst week is not a trade this project makes |
| Federated + differential privacy (50 synthetic clients) | DP σ=3.2, C=1.0: **ε 0.8417** at δ 1e-5, held-out MASE **1.2754** vs centralised **1.3372** (persistence 1.1150) | **not shipped** — no real clients, no privacy review; the epsilon is a claim about a simulator |

Reports: [day36-mood-forecasting.md](../ml/reports/day36-mood-forecasting.md),
[day37-jitai.md](../ml/reports/day37-jitai.md),
[day38-federated.md](../ml/reports/day38-federated.md). MLflow run ids are quoted
in each report (`37eebe38…`, `e3aa0567…`, `aba1806a…`).

The forecasting row is the report's most important negative result. A sequence
model was configured, trained and evaluated through the same harness as the
baselines; it lost; the product kept the deterministic descriptive range and says
in the UI that the range is *"not a calibrated confidence interval, probability
or medical advice"* ([ADR 0036](adr/0036-mood-forecasting.md)).

### 5.6 Fairness and cross-language parity

Five models are audited over 17 templated paired-sentence constructions (53
variants) across eleven dimensions: language, script, code-mixing, formality,
dialect spelling, gendered language, religion, caste, disability, sexuality and
region. Thresholds are dimension-specific (strictest 0.08 for religion and caste,
0.10 for region). The current run passes with a largest gap of **0.026**
(region-state) and per-dimension gaps of 0.008–0.026
([fairness.json](../ml/reports/fairness.json)).

Limitation, stated plainly: the audited models are **synthetic stand-in scorers**
on this machine. The audit proves the gate plumbing and the invariance harness, not
the fairness of a trained multilingual network. That requires the model, a device
and native speakers.

The browser parity gate is a different kind of fairness: the Python and TypeScript
safety engines must agree, phrase for phrase. 500/500 phrases identical, 56
code-point normalisation disagreements pinned as a table, 1,671 casefolded code
points compared ([day41-browser-private-ai.md](verification/day41-browser-private-ai.md)).

### 5.7 Agent, voice and share

- **Agent navigator**: 40/40 scenarios across ten categories, including four
  injection attempts, three confirmation-gating invariants (an effecting tool
  never executes without a confirmation; the confirmation event precedes the
  execution event; read-only tools never ask) and four out-of-scope refusals
  (no browsing, no sending messages, no payments, no booking)
  ([agent_eval.json](../evals/reports/2026-10-20/agent_eval.json)).
- **Voice**: p95 time-to-first-audio **98.614 ms** against a 1,500 ms budget, with
  the audio stages replaced by a deterministic fake; the real device path
  (microphone, WebAudio, barge-in) is unmeasured here
  ([day40](verification/day40-realtime-voice.md)).
- **Clinician share**: the FHIR R4 bundle is validated by the official HL7
  validator in CI; on this machine the offline structural check runs and says so.
  The end-to-end journey (consent → link → passcode → open → revoke → 404)
  passes at the API level ([day49](verification/day49-verification.md)).

### 5.8 Engineering measurements

| Measure | Value | Command |
| --- | --- | --- |
| Backend tests / coverage | **5,524 passed, 2 skipped**; 94.28 % statements, 85.50 % branches; safety gate green | `make test` |
| Frontend tests | **2,917 passed, 3 skipped** (135 files) | `npm run test:run` |
| Lint / types | ruff, ruff format (488 files), mypy (418), ESLint, `tsc --noEmit` — clean | `make lint` |
| ML tests | **553 passed, 1 skipped** | `make ml-test` |
| Initial JS | **133.1 KB gzip** of 135 KB budget; precache 744.5 KB of 800 KB | `npm run budget` |
| Journey | **13/13** API-level steps | `scripts/verify_day49_journey.py` |
| Lighthouse / CWV | **not measured** — no browser installable in the build sandbox | `npm run lighthouse` (CI) |

Details and the budget rationale are in [performance.md](performance.md).

---

## 6. Privacy and responsible AI

### 6.1 Data minimisation by construction

Sensitive fields (message text, journal bodies, mood notes, share documents) are
encrypted at rest with AES-256-GCM under per-user envelope keys
([ADR 0003](adr/0003-data-layer-models-migrations-and-encryption.md),
[ADR 0017](adr/0017-encrypted-journal-and-envelope-encryption.md)). The structured
logger drops raw text keys before writing, and an end-to-end test asserts it. The
user id never enters a prompt, and a test asserts that identifying text does not
reach a provider. Export and deletion are first-class: an export is a ZIP
attachment containing everything, deletion is re-authenticated with a typed
confirmation phrase and returns a receipt ([ADR 0022](adr/0022-privacy-controls-and-data-export.md)).

### 6.2 A share the server cannot read

The clinician-share bridge is the design's most interesting privacy statement.
The document is encrypted with AES-256-GCM under a key derived (HKDF-SHA256) from
a secret delivered in the URL fragment, the passcode and the token; the database
stores the token's SHA-256 and a PBKDF2 hash of the passcode, and **has no column
that could hold the key** ([ADR 0047](adr/0047-clinician-share-bridge.md),
[clinical-sharing.md](clinical-sharing.md)). Unknown, expired, revoked and
view-exhausted links all answer the same `404 share_unavailable`, because telling
them apart would be an enumeration oracle. Tokens, secrets and passcodes are
redacted from logs and error reports.

### 6.3 On-device mode as a privacy claim that can be checked

Private Mode is the only design in this project that makes "nothing leaves the
device" a property rather than a policy. It is checkable: a Playwright run with
the network blocked passes 4/4 at two viewports with **0 network attempts** during
the conversation, and an IndexedDB dump after a stored turn contains only
ciphertext, ids, ivs and key ids ([day41](verification/day41-browser-private-ai.md)).
Its honest limit is in §7: no model is pinned, so the reply slot is templates.

### 6.4 Governance artefacts

- **Risk register** ([ai-risk-register.md](ai-risk-register.md)): 21 risks with
  likelihood × severity, owner, mitigation, status and gate; the five
  highest-severity are pinned at the top (missed crisis, privacy leak,
  over-reliance, bias, drift).
- **Transparency page** (`/transparency`, 14 component cards × 5 headings each,
  localised in en/hi/bn with a key-parity test) — reachable before onboarding.
- **Kill switches** (12 flags, in-memory, audited, no restart) with a tested safe
  fallback per flag.
- **Drift monitoring** on privacy-only aggregates (length histogram, language mix,
  embedding centroid, confidence/safety/abstention rates); no text, no per-user
  rows ([day48](verification/day48-verification.md)).
- **Model and dataset cards** for every model that is described anywhere public
  (`ml/reports/day48-model-card-*.md`, `ml/data/cards/`).

### 6.5 What is deliberately absent

No analytics, no third-party scripts (a CI budget asserts zero third-party
requests), no social feed, no advertising, no background microphone, no
"engagement" metric, and no intervention that optimises for time in app. The
absence is the responsibility decision.

---

## 7. Limitations

Stated without softening, in rough order of what would matter most to a user:

1. **It has not been clinically reviewed.** The crisis copy, the helplines, the
   knowledge base and the presentation of PHQ-9/GAD-7 are unreviewed by a
   licensed clinician. Four helpline entries are still flagged
   `needs_verification`, and every entry's `last_verified` date is 2026-10-09.
2. **The translations are unreviewed.** Hindi and Bengali copy, including the
   crisis card, has been written by the author and checked for key parity, not by
   native speakers ([translation status](../translations/STATUS.md)).
3. **Recall-first costs precision, and users feel it.** 31/51 benign dev cases are
   escalated; in the live journey *"I had a long day and I am tired."* produced the
   crisis card. Recalibration needs real data and a clinical view of the trade.
4. **Every number describes synthetic text.** The crisis cases, emotion cases and
   response scenarios were hand-written by the author. They contain no real user
   language, no dialect variation beyond what one person imagined, and no
   multi-turn context. Real-world performance is unknown.
5. **No neural model beats its baseline here.** The mood transformer lost to a
   moving average; the retrieval encoder's gains are inside its own confidence
   intervals; the fine-tuned variants are not deployable because they cannot
   abstain.
6. **The safety backstop is TF-IDF, not a transformer.** The distilled
   safety classifier is a manifest slot marked `planned`; a general toxicity model
   is not a substitute.
7. **On-device inference is unmeasured.** No model is pinned, no tokens/second,
   load time or memory figure exists, and WebLLM's own cache is not covered by the
   integrity check.
8. **Voice, biofeedback and XR were verified with fakes.** No microphone, WebGPU
   adapter, headset or Bluetooth device exists in the build environment; the
   timings are of the code path, not of a device.
9. **Browser-level verification did not run for v2.0 in this environment.**
   Playwright and Lighthouse could not be installed (their CDNs are unreachable),
   so the E2E and Core Web Vitals claims in this report rest on CI configuration
   and on the previous committed runs, not on a run of this verification. What did
   run is recorded in [day49](verification/day49-verification.md).
10. **Scale is unknown.** There is no load test in this repository against
    production infrastructure, and the deployment has not served real traffic.
11. **No age policy.** There is no age gate and no policy for minors, which for
    this product category is a gap that must close before launch.
12. **Some gaps are known and parked, not hidden** — the full list, with the
    reason each is parked, is in [PROGRESS.md](../PROGRESS.md).

---

## 8. Future work

Ordered by what would change the most, not by what is most interesting:

1. **Clinical and native-speaker review** of the crisis copy, helplines, screening
   presentation and all Hindi/Bengali strings, with sign-off recorded in
   [clinical-review.md](clinical-review.md).
2. **Recalibrate the safety ensemble against consented real-world text** — the
   precision problem in §3.3 is the largest user-facing defect in the product.
3. **A pinned, measured on-device model**: run `npm run models:pin`, install
   transformers.js/WebLLM, and record tokens/second, first-token latency, memory
   and thermal behaviour on real hardware before claiming Private Mode does
   inference at all.
4. **A real multilingual safety classifier** (the empty manifest slot), with the
   same raise-only contract, evaluated per language, script and code-mixing mode
   with native-speaker-labelled data.
5. **A user study with ethics approval** — the only way to learn whether the
   tools help, whether the crisis card is usable, and what people actually type at
   3 a.m.
6. **Wire the 12 kill switches into every endpoint** with a contract test per
   fallback (today the store and the API are proved; not every endpoint branches).
7. **Per-region helplines in Private Mode** (currently the international set), and
   a verifying helpline job that fails when `last_verified` ages out.
8. **Browser-level verification in the release gate**: Playwright and Lighthouse
   in CI on the exact commit that is tagged, with the results attached to the
   release.
9. **Accessibility audit by disabled users**, especially for the calm space and
   the sensory profile, where the design assumptions are strongest and least
   tested.
10. **Push reminders and a native wrapper** — deliberately last: both add
    notification surfaces that this project has not yet reasoned about.

---

## 9. Artefacts and references

Every claim above resolves to one of these. Commands are the ones that produced
the numbers; all were run on 2026-10-20 unless a date is given.

**Product and design**

- [README](../README.md) — the user-facing summary and the AI/ML results table.
- [AGENTS.md](../AGENTS.md) — the non-negotiable rules and the ML ground rules.
- [docs/architecture.md](architecture.md) — request path, sequence diagrams, ML
  platform, on-device path, offline model.
- [docs/assets/v2-architecture-poster.svg](assets/v2-architecture-poster.svg) —
  the one-page poster.
- [docs/adr/](adr/README.md) — 47 architecture decision records.
- [docs/safety-design.md](safety-design.md), [docs/model-card.md](model-card.md),
  [docs/privacy-and-threat-model.md](privacy-and-threat-model.md),
  [docs/ai-risk-register.md](ai-risk-register.md),
  [docs/ethics-and-limitations.md](ethics-and-limitations.md),
  [docs/clinical-review.md](clinical-review.md).

**Evaluation**

- [docs/evaluation-report.md](evaluation-report.md) — method, gates, intervals.
- [evals/reports/2026-10-20/](../evals/reports/2026-10-20/) — `make eval`
  (safety 1.000/0.543, emotion 0.804, response 60/60) and `make eval-agent`
  (40/40).
- [ml/reports/day35-retrieval.md](../ml/reports/day35-retrieval.md) — retrieval.
- [ml/reports/day36-mood-forecasting.md](../ml/reports/day36-mood-forecasting.md) —
  run `37eebe3842f3493cb252f4b0740c6711`.
- [ml/reports/day37-jitai.md](../ml/reports/day37-jitai.md) — run
  `e3aa0567709149068a55b5fb25defa40`.
- [ml/reports/day38-federated.md](../ml/reports/day38-federated.md) — run
  `aba1806ad18e48a8959065639f04e14d`.
- [ml/reports/fairness.json](../ml/reports/fairness.json) —
  `python ml/scripts/check_fairness_gates.py`.
- [ml/reports/day34-safety-compression.md](../ml/reports/day34-safety-compression.md),
  [day34-emotion-compression.md](../ml/reports/day34-emotion-compression.md).

**Verification notes**

- [day49-verification.md](verification/day49-verification.md) — this release.
- [day48-verification.md](verification/day48-verification.md),
  [day48-redteam.md](verification/day48-redteam.md).
- [day47-clinician-share-bridge.md](verification/day47-clinician-share-bridge.md),
  [day46-xr-sensory.md](verification/day46-xr-sensory.md),
  [day44-agentic-care-navigator.md](verification/day44-agentic-care-navigator.md),
  [day41-browser-private-ai.md](verification/day41-browser-private-ai.md),
  [day40-realtime-voice.md](verification/day40-realtime-voice.md),
  [day35-retrieval.md](verification/day35-retrieval.md).
- [docs/redteam-report.md](redteam-report.md) — 150 scripted attacks.

**Data and models**

- [ml/data/CATALOGUE.md](../ml/data/CATALOGUE.md),
  [ml/data/cards/](../ml/data/cards/), [ml/configs/release_gates.yaml](../ml/configs/release_gates.yaml),
  [ml/dvc.yaml](../ml/dvc.yaml).
- [ml/reports/day48-model-card-*.md](../ml/reports/) — six model cards.
- [docs/performance.md](performance.md) — budgets and lazy-loading.

**Scripts**

- [`make lint`, `make test`, `make eval`, `make eval-agent`](../Makefile)
- [`make ml-test`, `make ml-smoke`, `make ml-mood-forecast`, `make ml-jitai-sim`,
  `make ml-federated-sim`, `make ml-retrieval-smoke`](../ml/Makefile)
- [`scripts/verify_day49_journey.py`](../scripts/verify_day49_journey.py)
- [`npm run build`, `npm run budget`, `npm run lighthouse`](../frontend/package.json)

---

*This report describes a portfolio project. It is not a clinical validation, not
a regulatory submission, and not evidence that Manovia helps anyone. If you are
in danger or in crisis, call your local emergency number now.*
