# Model card — Personalised intervention bandit

| | |
| --- | --- |
| Card version | 1.0 (2026-10-20) |
| Status | research only — offline seam, not wired to /plan/today |
| Run id | f329789262f64a4ab624e0e72dd1ca42 (Day 37) |
| Config | `ml/configs/bandit.yaml` |

## 1. What it is

8 reviewed exercises + 3 actions; closed numeric/time/emotion/history/preference features; bounded pre/post reward + abandonment penalty; per-user Bayesian linear posteriors warm-started from population; epsilon-greedy/LinUCB/Thompson; outer safety wrapper gates MEDIUM+ and PHQ-9 item-9.

## 2. Evaluation

20 seeds, 108k interactions: learned policies beat random (regret halved). IPS/DR within 0.006 of ground truth. Group gaps <0.15 (not fairness cert).

## 3. Limits

Synthetic users only; no durable store, no live opt-in; clinical review not done.

## 4. Kill switch

`bandit=false` → deterministic plan; why-this still shown. No learner called.
