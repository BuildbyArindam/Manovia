# Day 36 — Mood forecasting with sequence models and honest uncertainty

Research run `94aec4c8845c4768b4138aad7c68299e`; generated 2026-10-15 from config `mood_forecast_sequence_models` (seed `20261015`).

> **Research only.** The panel is synthetic; these numbers do not describe Manovia users. No trained weights are served by the product. The user-facing Outlook uses a separate, deterministic personal-history baseline and remains opt-in.

## Data and split

- Generator: `state-space-v1`; SHA-256: `82f956d8a8aef686c7be0e02fe50e6267bd018a28473700d4e430446cc290c3c`.
- 72 synthetic users; 210 calendar days each; 11812 observed check-ins across 15120 calendar rows. Missing outcomes were masked, not imputed.
- Training users: 43; calibration users: 14; held-out test users: 15. User groups are disjoint; target windows are forward-only and remain within each temporal segment.
- Temporal boundaries (calendar-day indices; start-inclusive, end-exclusive): fit targets [0, 101); validation targets [101, 130); calibration targets [130, 172); held-out test targets start at 172.
- Inputs: last 28 daily values/missingness, valence, energy, sleep tag, exercise, closed factor tags and weekday; outputs: next three daily valence 10/50/90 quantiles.

## Baseline and sequence-model comparison

Intervals were expanded to a nominal 80% using horizon-specific split-conformal scores from the separate calibration-user group. Coverage and width below are measured on the untouched held-out users. This is a descriptive calibration exercise: rolling-origin windows overlap, so repeated target scores are dependent. We do not claim a finite-sample conformal coverage guarantee.

| model | MAE ↓ | MASE vs seasonal naive ↓ | actual / nominal coverage | interval width | pinball loss ↓ | test targets |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `persistence` | 0.601 | 0.994 | 92.3% / 80% | 1.873 | 0.190 | 1301 |
| `moving_average_7d` | 0.554 | 0.915 | 78.3% / 80% | 1.609 | 0.175 | 1301 |
| `seasonal_naive` | 0.641 | 1.024 | 92.5% / 80% | 1.869 | 0.195 | 1301 |
| `exponential_smoothing` | 0.558 | 0.925 | 77.6% / 80% | 1.636 | 0.175 | 1301 |

**Neural result: not measured.** The configured GRU, LSTM and Transformer were not trained because PyTorch was unavailable. No claim that a neural model beats the baseline is possible from this run.

### Nominal vs actual interval coverage

Nominal marginal coverage is 80%; see each table row for held-out actual coverage and mean interval width. Per-horizon values are recorded in the MLflow summary artifact. Coverage is an empirical result on this simulator, not a guarantee for an individual or a formal finite-sample coverage claim: overlapping rolling-origin windows induce dependent target scores.

## Feature ablation

Not evaluated: PyTorch is unavailable, so neural feature ablations could not run. Baselines do not consume those feature groups; their values would not be an informative ablation.

## Few-shot last-layer personalisation

Not evaluated: neural weights were unavailable. The update code and optional tests are included.

## Cold-start serving policy (separate from this research model)

The product Outlook requires explicit opt-in, at least 14 distinct numeric check-in days in the latest 28 local calendar days, and a latest check-in no more than seven days old. Histories of 5 days are withheld. The research sequence models are never loaded into the API. The endpoint only summarizes recent numeric valence and does not read journal/free-text data.

## Reproducibility and limitations

- MLflow run: `94aec4c8845c4768b4138aad7c68299e`; config fingerprint and environment are logged.
- Dataset manifest: 82f956d8a8aef686c7be0e02fe50e6267bd018a28473700d4e430446cc290c3c (synthetic generated rows; no panel file or weights were written to Git).
- Assumptions: weekly sine seasonality; individual slow trend and factor effects; user-specific AR(1) noise; logging missing-at-random given weekday/user rate.
- Synthetic data cannot represent real variation, cultural context, measurement error, MNAR logging or clinical meaning. Valence is not a diagnosis or risk score.
- No neural quality claim, clinical review, external validation or deployment approval is implied by a successful baseline run.
