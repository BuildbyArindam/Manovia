# ADR 0036 — Mood forecasting: sequence research, honest ranges, and an opt-in personal-history Outlook

**Status:** Accepted (Day 36)

**Date:** 2026-10-15

## Context

Mood check-ins provide a five-point numeric valence and energy rating, a small set of closed tags, and optional encrypted notes. A three-day outlook could help someone reflect on their recent pattern, but a forecast in a mental-wellbeing product can easily sound more certain, clinical, or causal than its evidence warrants.

There is no suitable real-user dataset in this repository. PyTorch is also unavailable in the build sandbox; its large wheel and CUDA dependencies were deliberately not installed. A green run therefore cannot be described as evidence that a learned sequence model improves on a baseline. We still need a reproducible research harness that can compare such models on a future research machine without leakage, and a tightly scoped product feature that does not pretend the unrun research model is ready to serve.

## Decisions

### 1. Keep the research experiment separate from the product endpoint

`ml/src/manovia_ml/mood_forecast/` contains a deterministic synthetic-data experiment. `backend/app/services/mood/outlook.py` is a separate, transparent calculation over a person's own recent numeric history. The API never imports the sequence-model package, loads research weights, or sends a user's data to an MLflow run.

The synthetic panel is not representative of Manovia users and is never used as a user-facing history. It exists to test split logic, training mechanics, metrics, uncertainty calibration and report generation.

### 2. Make leakage boundaries explicit in the research pipeline

- The simulator generates 72 synthetic users × 210 calendar days with explicit missing check-ins; missing outcomes are masked, not filled in as labels.
- User groups are disjoint: 43 train, 14 calibration and 15 held-out test users. Training users have forward-only fit and validation periods. Target windows may not cross a temporal boundary; tests pin the start-inclusive/end-exclusive cut points.
- The feature window is the previous 28 days. The research task predicts the next three daily valence values as ordered 10/50/90 quantiles. GRU, LSTM and Transformer models are optional PyTorch implementations; persistence, 7-day moving average, seasonal naive and exponential smoothing are the baselines.
- Baselines and trained models use the calibration-user group for horizon-specific split-conformal score expansion toward nominal 80% coverage. The test-user group is used only for final scoring. Rolling-origin windows overlap, so repeated target scores are dependent; actual coverage is descriptive on this simulator, and no finite-sample conformal or individual guarantee is claimed.
- Feature ablations and guarded last-layer personalization are in the experiment code. They are reported as not measured when neural fitting is unavailable; a missing score is never fabricated or substituted with a baseline score.

Each run records its config, seed, environment, synthetic-data hash, split counts, metrics and artifacts in MLflow. The report and config are committed; generated panel bytes, MLflow stores and model weights are not.

### 3. Treat absence of PyTorch as a blocking limitation, not a benchmark result

The command refuses a baseline-only run unless `--allow-baselines-only` is explicit. With that flag, the report says that the neural result, neural feature ablations and few-shot adaptation were not measured. The current sandbox run therefore supports only a baseline comparison. It does **not** support a neural performance claim or a recommendation to deploy a sequence model.

### 4. Make product Outlook opt-in, numeric-only, and non-clinical

- A new persisted `mood_outlook_enabled` preference defaults to `false`. The Insights page asks for Settings first and does not call the Outlook endpoint while the user is opted out. The endpoint also enforces the setting server-side, after authentication and the existing terms/privacy consent gate, and before reading mood rows.
- The service receives only timestamps, numeric valence/energy and closed tags from the caller's own check-ins. It does not read or decrypt mood notes, journal text, chat text, or assessment answers.
- The estimate is withheld unless there are at least 14 distinct local-calendar check-in days in the latest 28 days and the latest check-in is no more than seven days old. Multiple entries on one day do not satisfy multiple days; missing days are not imputed.
- The product estimate is a simple weighted recent/overall personal-history baseline. The displayed range is derived from the person's recent empirical variability, with a minimum width and a horizon widening rule. It is **not** calibrated by the research pipeline, is not a confidence interval or probability, and can be wrong.
- Closed-tag summaries require at least five tagged and five other days and a mean difference of at least 0.5 points. They describe an association in these entries, not a cause or explanation. The feature is not a risk classifier, diagnostic instrument, health advice or safety intervention.
- English, Hindi and Bengali copy keeps the uncertainty, non-medical limitation and user choice explicit. Small-action/support suggestions are gentle and optional; they are not triggered by an inference of crisis.

### 5. Present uncertainty accessibly

The frontend renders the estimate and lower/upper range in a semantic table with a caption and row/column headers. It does not rely on a chart, color, or an unlabeled visual band to communicate the range. The uncertainty and non-medical notes are associated with the named card for assistive technology. A person can turn the feature off in Settings at any time.

### 6. Include the new preference in the owner's export

The privacy export now includes `user-settings.json`, containing planner preferences and the Outlook opt-in. The archive format is `manovia-export-2`; the earlier Day-22 verification remains a record of v1. The Outlook preference is also removed through the existing user-settings cascade when an account is deleted.

## Consequences

- The repository has a fully testable research pipeline and a modest opt-in product baseline, but no evidence that a neural sequence model beats a baseline on real or synthetic data. The synthetic baseline table is not a clinical or product-quality result.
- A supported research environment must install the optional training extra and run the sequence experiment before any neural result can be stated. A real-world validation study, native-language review and clinician review remain separate requirements before any public or clinical claim.
- The product can show an estimate only for a narrow slice of users with enough recent check-ins. That is an intentional holdback, not a blank to be filled with population averages.
- The product range must always be called a rough personal variation range in user-facing copy; changing the UI to say “confidence interval,” “chance,” or a clinical prediction would contradict this decision.

## Verification

- Research output: [`ml/reports/day36-mood-forecasting.md`](../../ml/reports/day36-mood-forecasting.md), with the actual run ID, baseline results, split boundaries and blocked neural status.
- Commands, test outcomes, environment limitations and manual follow-up: [`docs/verification/day36-mood-forecasting.md`](../verification/day36-mood-forecasting.md).
- API contract: [`docs/api.md`](../api.md), generated from the app; the operation-count and byte-for-byte drift tests are in `backend/tests/unit/test_api_docs_export.py`.
