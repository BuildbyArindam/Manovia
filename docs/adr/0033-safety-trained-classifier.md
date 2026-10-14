# ADR 0033: Training the safety classifier — cost-sensitive loss, calibration, conformal abstention, explainability

- Status: accepted (Day 33)
- Date: 2026-10-14
- Deciders: Manovia maintainers
- Supersedes: none. Extends [0009 safety ML ensemble](0009-safety-ml-ensemble.md) with a *trained* classifier behind the same interface, and [0031 ML platform](0031-ml-platform.md) with the safety-specific run
- Related: [0008 crisis detection rules engine](0008-crisis-detection-rules-engine.md), [0009 safety ML ensemble](0009-safety-ml-ensemble.md), [0023 multilingual](0023-multilingual-english-hindi-bengali.md), [0024 evaluation harness](0024-evaluation-harness.md), [0028 model card and clinical review gate](0028-model-card-and-clinical-review-gate.md), [0031 ML platform](0031-ml-platform.md), [0032 emotion LoRA fine-tune](0032-emotion-lora-finetune.md), [`ml/data/CATALOGUE.md`](../../ml/data/CATALOGUE.md), [`docs/model-card.md`](../model-card.md), [`docs/safety-design.md`](../safety-design.md) §12.8–§12.9

## Context

Day 9 shipped a safety ML backstop: a char n-gram TF-IDF model into multinomial
logistic regression, trained on 340 hand-written cases, with three raise bands
whose thresholds were grid-searched on dev afterwards. It works — crisis recall on
dev is 1.000 against a rules-only 0.333 — and it has four properties that Day 33
exists to change:

1. **The error costs are implicit.** Cross-entropy with `class_weight='balanced'`
   is a scalar approximation of "missing a crisis is worse than a false alarm".
   It cannot express that a missed IMMINENT is worse than a missed HIGH, nor that
   escalating a benign message is worse than escalating a LOW one.
2. **The decision rule is not the training objective.** The model is trained to
   maximise likelihood and then asked a different question at inference
   (is `P(high)+P(imminent)` above a floor?). The number that gets compared to a
   threshold was never the number being optimised.
3. **There is no stated uncertainty.** The classifier returns a level and a
   confidence. It cannot say "I cannot rule out MEDIUM", which is the honest
   answer for most borderline messages and the one the suspicion band is
   hand-approximating with a probability floor.
4. **There is no way to catch shortcut learning.** A model at 1.000 recall that
   keys on one word looks identical, in the metrics, to a model that understood
   the message.

Constraints, unchanged from Day 31: **no GPU and no Hugging Face access in the
build sandbox**; no dataset rows or model weights may ever be committed; every
dataset must be in the catalogue with a verified licence before use; every run
logged to MLflow with the seven reproducibility things; config is data with
unknown keys as errors; **the rules engine stays the gate and no model may lower a
level the rules found**; and training data is synthetic and hand-written only —
never real people's crisis messages.

## Decision

### 1. The cost matrix is the specification, and it lives in code, not in config

`COST_MATRIX[true][predicted]` in `ml/src/manovia_ml/training/safety/taxonomy.py`,
row-normalised, over `none < low < medium < high < imminent`:

| truth \ predicted | none | low | medium | high | imminent |
| --- | --- | --- | --- | --- | --- |
| none | 0 | 1 | 3 | 8 | 20 |
| low | 1 | 0 | 2 | 6 | 16 |
| medium | 4 | 2 | 0 | 5 | 12 |
| high | 12 | 8 | 5 | 0 | 3 |
| imminent | 25 | 16 | 10 | 4 | 0 |

The worst cell is a missed IMMINENT (25); the cheapest off-diagonal cell is 1.
**The asymmetry is deliberately narrow — 25 against 20, not a 10× factor** — and
this is the decision most likely to be argued with, so it is stated with its
reasoning in `docs/safety-design.md` §12.8.1. A matrix that made over-flagging
nearly free would train a model that escalates almost everything; a symmetric
matrix would be cross-entropy and would not encode the bias at all.

The matrix is in code rather than in config because it is a **product decision,
not a hyperparameter**. What is config-resident is *whether* it is used, whether
the loss is row-normalised, how much label smoothing is applied to the targets
before the cost lookup, and whether the inference-time decision rule is the
expected-cost argmin or a plain argmax. Adding a second matrix is an act with a
name, not a YAML edit.

The inference-time point prediction is the Bayes argmin under the same matrix
(`expected_cost_level`), so the model is trained and asked the same question.

### 2. Base model: `FacebookAI/xlm-roberta-base` (MIT), LoRA rank 16

The same base as the emotion run (ADR 0032), verified through the Hub API on
2026-10-13: MIT, 278,885,778 parameters, ~100 languages including en/hi/bn. The
per-language numbers this classifier needs are only possible from a model whose
tokenizer covers Devanagari and Bengali natively, and sharing the base with the
emotion model means one tokenizer, one upgrade path, one licence review.

LoRA on `query`/`value` (rank 16, alpha 32) rather than a full fine-tune: the
trainable share is ~0.6%, the artefact is small, and the base weights stay
untouched and re-checkpointable.

**Not run here.** No GPU, no hub access. What was run is the same code path
against `tiny://xlm-roberta-base` (a 2-layer random-init stand-in) on 20
hand-written rows per split. The ready-to-run path is
`ml/notebooks/colab_safety_lora.ipynb` + `ml/configs/safety_lora_xlmr.yaml`.

### 3. The corpus is expanded from the Day-9 seeds, with provenance on every row

456 hand-written Day-9 train/dev seeds expand through a template paraphraser and
adversarial augmentation (typos, spacing tricks, romanised Hindi, code-mixing,
emoji) to **3,445 kept rows**, with **127 dropped as `rules_inconsistent`** — the
rules engine is stricter than the row's label, which is a contradiction this
pipeline cannot resolve. A *more lenient* engine is not a drop: that gap is what
the model exists to close, and a test asserts it.

Every row carries `seed_id`, `source`, `transforms`, `rules_level` and
`rules_agrees`, so any row can be traced back to the seed it came from and
dropped. The frozen Day-9 **test** split is never read by the builder; its hash
lives in `evals/datasets/manifest.json`, and the trainer now **refuses** to run if
a training row appears verbatim in the held-out split (checked before the first
optimiser step).

Rejected: scraping real crisis text. It is the obvious way to get 100k rows and it
is forbidden by the Day-31 ground rules and by this ADR — real people's crisis
messages are not training data for a product that will be shown to people in
crisis.

### 4. Calibration is temperature scaling, fitted on a split that does nothing else

A fourth split, **calibration**, exists for the temperature and the conformal
quantile and for nothing else. Dev selects the model (early stopping on crisis
recall); test is the measurement. A split that does two of those jobs can do
neither.

Temperature scaling is one scalar fitted by golden-section search on the NLL:
one-dimensional, derivative-free, deterministic, no scipy dependency, and searched
in log space so a wandering search cannot produce a sign flip that inverts the
ranking while making NLL look better. The report prints ECE before/after and the
reliability bins.

The contract is asserted in both directions: a confidently *wrong* model gets
`T > 1`, and a well-separated model may be left *sharper* (`T <= 1`), because the
search minimises NLL and a confident correct prediction has lower NLL when
sharper. A fitter that only knows how to soften cannot pass the suite.

Rejected: isotonic and Platt regression. Isotonic needs more held-out rows than
this corpus has per level, and Platt adds a slope parameter that a 3,445-row
corpus cannot pin down without overfitting the very thing it is meant to fix.

### 5. Conformal prediction sets at 95%, and the abstention policy is a MEDIUM floor

Split conformal on the calibration split, `alpha = 0.05`, with the
finite-sample correction (`ceil((n+1)(1-alpha))/n`-th order statistic). At n=10
that correction is the difference between "about 95%" and "at least 95%", and it
is also why a small calibration split buys its guarantee with abstention — which
the report prints next to the coverage so the cost is visible.

The policy:

```
abstain_level(set, point) = point, or MEDIUM if the set contains any level >= MEDIUM
```

It never lowers the point prediction, and it **never promotes to the most severe
level in the set**. `{medium, imminent}` acts as MEDIUM, not IMMINENT: the set
lists what could not be ruled out, and how far above MEDIUM a message goes is the
point prediction's job. Promoting to the worst plausible level would turn every
uncertain row into a crisis card — the alarm-fatigue failure §12.6 already
measures from the other side. A property test covers all five point levels ×
five set shapes, in both `ml/` and `backend/`, and the backend's golden vectors
are duplicated in the ML suite so the two mirrors cannot drift.

In the ensemble this reaches the user through the **existing suspicion band**:
the abstention level is what the band's mass comparison sees, so a cautious model
produces a MEDIUM check-in and a confident one produces nothing extra. **The three
raise bands, the pragmatics gate and `max(rules, ml)` are unchanged.**

Rejected: promoting to the worst set member (above), and a hard abstention that
returns "I don't know" to the user. There is no product surface for "I don't
know" in a crisis path; there is a MEDIUM check-in, and it is the right amount of
caution for an uncertain row.

### 6. Explainability is a developer tool with three named shortcut signals

`ml/src/manovia_ml/training/safety/explain.py` computes Integrated Gradients for
flagged examples and reports:

1. **single-token dominance** — one token holding more than half the attribution
   mass;
2. **off-lexicon top token** — the biggest contributor is outside the language's
   small risk lexicon, so the model is keying on something a reviewer would not
   have chosen;
3. **copy-paste attributions** — two flagged examples from different categories
   or languages whose top-five words overlap heavily.

Integrated Gradients rather than SHAP: it satisfies **completeness** (the
attributions sum to the logit difference from the baseline), which makes "this
token contributed this much" a checkable claim rather than a saliency picture,
and it needs no background distribution. Special tokens are never attributed.

The output is a terminal/JSON tool for maintainers. **It must never be shown to a
user** — the module docstring says so, and the module refuses to run in the same
process as the trainer, because attributions must describe the artefact on disk
and not the model that was just fitted.

### 7. `SAFETY_ML=trained|baseline`, one selector, a visible fallback

`build_safety_classifier(settings)` is the single place that decides which
classifier the ensemble consumes, called by both the API dependency and the chat
orchestrator, so a deployment cannot end up with the chat path on one model and
the assess endpoint on the other. Order of checks:

1. `SAFETY_ML_ENABLED=false` → disabled, and the pipeline is exactly Day-8
   rules-only.
2. `SAFETY_ML=trained` → the packaged LoRA model. Missing directory, missing
   `torch`/`transformers`, or an unloadable artefact falls back to the **Day-9
   baseline** with a `+trained-unavailable` version suffix — **never to
   rules-only**.
3. `SAFETY_ML=baseline` (the default) → the Day-9 model.

The fallback is recorded rather than silent because "the ML is silently off" is a
different deployment from the one the operator asked for, and a version string is
the only thing that travels with a prediction. The eval harness reads it: a report
written with `--model trained` that did not load the trained model says so in a
block quote, because a report labelled "trained" whose every number came from the
baseline is worse than no report.

The backend bridge has **no logging call anywhere** — the safety package's privacy
test greps the sources including docstrings — so the failure reason travels in the
classifier object (`failure`, `describe()`) instead of a log line.

## Consequences

- The ensemble can now be pointed at a model whose training data, cost matrix,
  calibration split, conformal quantile and evaluation are all in this repository
  — and, just as importantly, can still run without it: `SAFETY_ML=baseline` is
  the default and a deployment that never sets the flag behaves exactly as Day 9.
- A number about the trained classifier will carry a run id, and that run carries
  the git commit, the split hashes, the resolved config, the seed, the device and
  the library versions.
- **No trained number exists yet.** Every Day-33 figure in this repository comes
  from the smoke path and is noise by construction; `docs/safety-design.md` §12.9
  lists what that leaves unestablished, and the model card §5.2b repeats it.
- The corpus is templated: 3,445 rows from 456 seeds is far less lexical diversity
  than the row count suggests, and the Day-9 hand-written set already contains
  near-paraphrases across the train/test boundary (max Jaccard 0.719, zero
  verbatim overlap). The catalogue entry records it; a future run should measure
  it again and say whether it got worse.
- The IMMINENT tier is the smallest and the most consequential, and the rules-only
  baseline already misses most of it on the frozen test split (0.275 against a
  0.330 gate floor — a documented gate failure, not a silent pass). The cost
  matrix states the intent; it is not evidence the tier is now detectable.
- The abstention policy's recall/precision trade is unmeasured on a real model.
  The smoke run's random model abstains on every row, which is correct behaviour
  for a model that knows nothing and says nothing about what the policy costs.
- Two things are deliberately **not** decided here: whether the trained classifier
  should ever be called on stored data (needs a privacy argument), and whether a
  second base model (MuRIL) should ship alongside XLM-R (needs the three-way
  comparison run on a GPU first, as in ADR 0032).

## Verification

Run in the sandbox, on this tree:

| check | result |
| --- | --- |
| `make safety-smoke` (`ml/`, tiny random-init model, CPU) | exit 0; prints HIGH+IMMINENT recall, macro-F1, abstention policy, temperature + ECE before/after, conformal target/calibration/held-out coverage, TF-IDF comparison, leakage |
| `pytest` in `ml/` (offline) | **323 passed** — 102 of them new for Day 33 (corpus, calibration, conformal, trainer, model layout, explain) |
| `pytest tests/safety/` in `backend/` (offline) | **826 passed** — including the new trained-classifier bridge suite and the `--model` flag suite |
| `ruff check` + `ruff format --check` + `mypy` in `ml/` and `backend/` | clean (77 files / 360 files respectively) |
| `python evals/run_safety_eval.py --split test --model baseline` | precision 0.567 [0.463, 0.670], recall 1.000 [1.000, 1.000]; rules-only recall 0.275 |
| `python evals/run_safety_eval.py --split test --model trained` | identical numbers, **because the trained artefact does not exist** — and the report says so in a block quote |
| `dvc status` in `ml/` | up to date (toy split regenerated after three near-duplicate fixture rows were rewritten) |
| a real GPU run (`xlm-roberta-base`, the expanded corpus) | **not run here** — no GPU, hub unreachable; `ml/notebooks/colab_safety_lora.ipynb` is the ready-to-run path and the PR lists what the owner must check by hand |
