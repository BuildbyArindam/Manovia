# Model card — Mood forecasting (Outlook)

| | |
| --- | --- |
| Card version | 1.0 (2026-10-20) |
| Status | experimental — synthetic, not served as learned weights |
| Run id | 94aec4c8845c4768b4138aad7c68299e (Day 36) |
| Config | `ml/configs/mood_forecast.yaml` |
| Data | 72 synthetic users × 210 days, user-disjoint forward splits |
| Owner | Manovia maintainers |

## 1. What it is

Sequence baselines (persistence, 7-day MA, seasonal, exp smooth) + unmeasured GRU/LSTM/Transformer quantiles (10/50/90). Product Outlook is deterministic personal-history baseline (not learned weights), default-off opt-in.

## 2. Intended use

Rough 3-day variability estimate from numeric check-ins only. Range is not CI/probability.

## 3. Evaluation

Best baseline 7-day MA: MAE 0.554, MASE 0.915, empirical coverage 78.3%/80%. Neural scores not measured (no torch). Interval expansion is descriptive, no formal guarantee.

## 4. Limits

- Synthetic only; no real-user validity; no clinical benefit claimed.
- Off until 14/28 + recency 7 days; needs clinical copy review.
- Copy explicitly says can be wrong, not medical advice.

## 5. Kill switch

`forecasting=false` hides Outlook card; check-ins unchanged.
