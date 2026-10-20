# Day 36 — Mood forecasting with sequence models and honest uncertainty

Research run `37eebe3842f3493cb252f4b0740c6711`; generated 2026-10-20 from config `mood_forecast_sequence_models` (seed `20261015`).

> **Research only.** The panel is synthetic; these numbers do not describe Manovia users. No trained weights are served by the product. The user-facing Outlook uses a separate, deterministic personal-history baseline and remains opt-in.

## Data and split

- Generator: `state-space-v1`; SHA-256: `82f956d8a8aef686c7be0e02fe50e6267bd018a28473700d4e430446cc290c3c`.
- 72 synthetic users; 210 calendar days each; 11812 observed check-ins across 15120 calendar rows. Missing outcomes were masked, not imputed.
- Training users: 43; calibration users: 14; held-out test users: 15. User groups are disjoint; target windows are forward-only and remain within each temporal segment.
- Temporal boundaries (calendar-day indices; start-inclusive, end-exclusive): fit targets [0, 101); validation targets [101, 130); calibration targets [130, 172); held-out test targets start at 172.
- Inputs: last 28 daily values/missingness, valence, energy, sleep tag, exercise, closed factor tags and weekday; outputs: next three daily valence 10/50/90 quantiles.

## Baseline and sequence-model comparison

Intervals were calibrated at 80% on the separate calibration-user group using horizon-specific split-conformal expansion. Coverage and width below are measured on the untouched held-out users.

| model | MAE ↓ | MASE vs seasonal naive ↓ | actual / nominal coverage | interval width | pinball loss ↓ | test targets |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `persistence` | 0.601 | 0.994 | 92.3% / 80% | 1.873 | 0.190 | 1301 |
| `moving_average_7d` | 0.554 | 0.915 | 78.3% / 80% | 1.609 | 0.175 | 1301 |
| `seasonal_naive` | 0.641 | 1.024 | 92.5% / 80% | 1.869 | 0.195 | 1301 |
| `exponential_smoothing` | 0.558 | 0.925 | 77.6% / 80% | 1.636 | 0.175 | 1301 |
| `gru` | 0.584 | 1.082 | 93.5% / 80% | 3.043 | 0.201 | 1301 |
| `lstm` | 0.621 | 1.212 | 87.4% / 80% | 3.016 | 0.210 | 1301 |
| `transformer` | 0.561 | 1.024 | 89.0% / 80% | 2.820 | 0.193 | 1301 |

**Result:** the best neural model (`transformer`, MAE 0.561) did not beat the best baseline (`moving_average_7d`, MAE 0.554) on held-out synthetic users. This is a synthetic benchmark, not a product-quality claim.

### Nominal vs actual interval coverage

Nominal marginal coverage is 80%; see each table row for held-out actual coverage and mean interval width. Per-horizon values are recorded in the MLflow summary artifact. Coverage is an empirical result on this simulator, not a guarantee for an individual.

## Feature ablation

| model | ablated group | MAE | change vs full model |
| --- | --- | ---: | ---: |
| `gru` | `context` | 0.598 | +0.013 |
| `gru` | `day_of_week` | 0.605 | +0.021 |
| `gru` | `energy` | 0.584 | -0.000 |
| `lstm` | `context` | 0.632 | +0.011 |
| `lstm` | `day_of_week` | 0.628 | +0.007 |
| `lstm` | `energy` | 0.622 | +0.001 |
| `transformer` | `context` | 0.570 | +0.009 |
| `transformer` | `day_of_week` | 0.614 | +0.053 |
| `transformer` | `energy` | 0.561 | +0.000 |

## Few-shot last-layer personalisation

### `gru`

Accepted 15 of 15 attempted held-out-user updates. Updates affected only the regularised, norm-bounded final linear layer and were reverted when a later guard block exceeded the loss tolerance.
Base test MAE: 0.584; few-shot test MAE: 0.565. Test outcomes were used only after adaptation for scoring. The later guard block could reject an update but was never optimised on. Intervals are not claimed for personalised forecasts.

### `lstm`

Accepted 15 of 15 attempted held-out-user updates. Updates affected only the regularised, norm-bounded final linear layer and were reverted when a later guard block exceeded the loss tolerance.
Base test MAE: 0.621; few-shot test MAE: 0.594. Test outcomes were used only after adaptation for scoring. The later guard block could reject an update but was never optimised on. Intervals are not claimed for personalised forecasts.

### `transformer`

Accepted 15 of 15 attempted held-out-user updates. Updates affected only the regularised, norm-bounded final linear layer and were reverted when a later guard block exceeded the loss tolerance.
Base test MAE: 0.561; few-shot test MAE: 0.568. Test outcomes were used only after adaptation for scoring. The later guard block could reject an update but was never optimised on. Intervals are not claimed for personalised forecasts.


## Cold-start serving policy (separate from this research model)

The product Outlook requires explicit opt-in, at least 14 distinct numeric check-in days in the latest 28 local calendar days, and a latest check-in no more than seven days old. Histories of 5 days are withheld. The research sequence models are never loaded into the API. The endpoint only summarizes recent numeric valence and does not read journal/free-text data.

## Reproducibility and limitations

- MLflow run: `37eebe3842f3493cb252f4b0740c6711`; config fingerprint and environment are logged.
- Dataset manifest: 82f956d8a8aef686c7be0e02fe50e6267bd018a28473700d4e430446cc290c3c (synthetic generated rows; no panel file or weights were written to Git).
- Assumptions: weekly sine seasonality; individual slow trend and factor effects; user-specific AR(1) noise; logging missing-at-random given weekday/user rate.
- Synthetic data cannot represent real variation, cultural context, measurement error, MNAR logging or clinical meaning. Valence is not a diagnosis or risk score.
- No neural quality claim, clinical review, external validation or deployment approval is implied by a successful baseline run.
