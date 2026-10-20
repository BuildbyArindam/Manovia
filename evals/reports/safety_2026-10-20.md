# Safety evaluation — 2026-10-20 (split: dev)

- Pipeline: rules engine + ML ensemble (Day-9 TF-IDF + calibrated logistic regression).
- ML model: `--model baseline`, loaded as v1
- Thresholds: min_confidence=0.7, crisis_mass_floor=0.3, suspicion_floor=0.25 (chosen on dev only — see `threshold_tuning.md`).
- Bootstrap: B=2000 resamples, 95% percentile CIs (seed=20261012).
- Cases: 116 from `crisis_cases_dev.jsonl`.

## Headline: HIGH+IMMINENT (the crisis tiers)

| pipeline | precision | recall | crisis cases | precision 95% CI | recall 95% CI |
| --- | --- | --- | --- | --- | --- |
| **rules + ML ensemble** | **0.543** | **1.000** | 51 | [0.441, 0.645] | [1.000, 1.000] |
| rules only | 0.810 | 0.333 | 51 | [0.609, 0.958] | [0.204, 0.471] |

Day 9 target: crisis recall >= 0.97 on dev — **MET** (observed 1.000, 95% CI [1.000, 1.000]).

Honest caveat: recall-first thresholds are the default; the false-alarm cost
on benign text at this dataset size is reported under "Cost of the recall
target" below and in `docs/safety-design.md` §12.6.

## CI gate status

Overall CI gate verdict: **PASS** (configured in `evals/thresholds.yaml`)

| check | threshold | observed | verdict | notes |
| --- | --- | --- | --- | --- |
| HIGH+IMMINENT recall (active pipeline) | `>= 0.970` | `1.000` | PASS | Primary crisis recall gate from evals/thresholds.yaml |
| HIGH+IMMINENT recall (rules-only baseline) | `>= 0.330` | `0.333` | PASS | Catches weakened/removed HIGH patterns even when ML backstop is enabled |
| Per-language HIGH+IMMINENT recall (min across en/hi/hi-Latn/bn) | `>= 0.950` | `1.000` | PASS | All languages above floor |
| No recall regression vs previous report | `delta >= 0.000` | `0 regressions` | PASS | No recall drop |

## Regression diff vs previous report

Compared against `evals/reports/safety_2026-10-13.md`:

| metric | previous | current | delta | status |
| --- | --- | --- | --- | --- |
| HIGH+IMMINENT recall (active) | 1.000 | 1.000 | +0.000 | **UNCHANGED** |
| HIGH+IMMINENT precision (active) | 0.543 | 0.543 | +0.000 | **UNCHANGED** |
| HIGH+IMMINENT recall (rules only) | 0.333 | 0.333 | +0.000 | **UNCHANGED** |
| HIGH+IMMINENT precision (rules only) | 0.810 | 0.810 | +0.000 | **UNCHANGED** |
| Crisis recall — English | 1.000 | 1.000 | +0.000 | **UNCHANGED** |
| Crisis recall — Hindi (Devanagari) | 1.000 | 1.000 | +0.000 | **UNCHANGED** |
| Crisis recall — Hindi (romanised) | 1.000 | 1.000 | +0.000 | **UNCHANGED** |
| Crisis recall — Bengali | 1.000 | 1.000 | +0.000 | **UNCHANGED** |
| Benign crisis escalations (count) | 31 | 31 | +0 | **UNCHANGED** |

## Per-level metrics (rules + ML ensemble)

| level | support | precision | recall | F1 | recall 95% CI |
| --- | --- | --- | --- | --- | --- |
| NONE | 38 | 0.733 | 0.289 | 0.415 | [0.154, 0.444] |
| LOW | 13 | — | 0.000 | — | [0.000, 0.000] |
| MEDIUM | 14 | 0.000 | 0.000 | — | [0.000, 0.000] |
| HIGH | 31 | 0.322 | 0.935 | 0.479 | [0.833, 1.000] |
| IMMINENT | 20 | 0.500 | 0.100 | 0.167 | [0.000, 0.250] |

## Per-level metrics (rules only, for comparison)

| level | support | precision | recall | F1 | recall 95% CI |
| --- | --- | --- | --- | --- | --- |
| NONE | 38 | 0.402 | 0.974 | 0.569 | [0.912, 1.000] |
| LOW | 13 | 0.500 | 0.077 | 0.133 | [0.000, 0.250] |
| MEDIUM | 14 | 0.000 | 0.000 | — | [0.000, 0.000] |
| HIGH | 31 | 0.500 | 0.290 | 0.367 | [0.136, 0.457] |
| IMMINENT | 20 | 0.333 | 0.050 | 0.087 | [0.000, 0.174] |

## Per-category metrics (rules + ML ensemble)

| category | total cases | crisis cases | crisis precision | crisis recall | crisis recall 95% CI | exact-level recall |
| --- | --- | --- | --- | --- | --- | --- |
| Suicidal ideation | 14 | 11 | 0.846 | 1.000 | [1.000, 1.000] | 0.786 |
| Intent / plan | 24 | 24 | 1.000 | 1.000 | [1.000, 1.000] | 0.375 |
| Self-harm | 8 | 5 | 0.625 | 1.000 | [1.000, 1.000] | 0.625 |
| Severe hopelessness | 13 | 1 | 0.091 | 1.000 | [1.000, 1.000] | 0.077 |
| Acute medical | 5 | 5 | 1.000 | 1.000 | [1.000, 1.000] | 0.000 |
| Abuse disclosure | 3 | 3 | 1.000 | 1.000 | [1.000, 1.000] | 1.000 |
| Harm to others | 2 | 2 | 1.000 | 1.000 | [1.000, 1.000] | 1.000 |

## Confusion matrix (rules + ML ensemble)

Rows are ground truth; columns are the scored pipeline's final level.

| true \ pred | NONE | LOW | MEDIUM | HIGH | IMMINENT | total |
| --- | --- | --- | --- | --- | --- | --- |
| **NONE** | 11 | 0 | 6 | 21 | 0 | 38 |
| **LOW** | 2 | 0 | 1 | 10 | 0 | 13 |
| **MEDIUM** | 2 | 0 | 0 | 12 | 0 | 14 |
| **HIGH** | 0 | 0 | 0 | 29 | 2 | 31 |
| **IMMINENT** | 0 | 0 | 0 | 18 | 2 | 20 |

## Per-language performance

| language | cases | crisis recall | crisis recall 95% CI | crisis precision | macro recall |
| --- | --- | --- | --- | --- | --- |
| English | 49 | 1.000 (22) | [1.000, 1.000] | 0.595 | 0.284 |
| Hindi (Devanagari) | 18 | 1.000 (8) | [1.000, 1.000] | 0.500 | 0.200 |
| Hindi (romanised) | 24 | 1.000 (10) | [1.000, 1.000] | 0.556 | 0.325 |
| Bengali | 25 | 1.000 (11) | [1.000, 1.000] | 0.478 | 0.225 |

Recall per level, per language (support in brackets):

| language | NONE recall | LOW recall | MEDIUM recall | HIGH recall | IMMINENT recall |
| --- | --- | --- | --- | --- | --- |
| English | 0.438 (16) | 0.000 (5) | 0.000 (6) | 0.857 (14) | 0.125 (8) |
| Hindi (Devanagari) | 0.000 (6) | 0.000 (2) | 0.000 (2) | 1.000 (5) | 0.000 (3) |
| Hindi (romanised) | 0.375 (8) | 0.000 (3) | 0.000 (3) | 1.000 (6) | 0.250 (4) |
| Bengali | 0.125 (8) | 0.000 (3) | 0.000 (3) | 1.000 (6) | 0.000 (5) |

Crisis recall per category per language (crisis cases in brackets):

| language | Suicidal ideation | Intent / plan | Self-harm | Severe hopelessness | Acute medical | Abuse disclosure | Harm to others |
| --- | --- | --- | --- | --- | --- | --- | --- |
| English | 1.000 (5) | 1.000 (12) | 1.000 (2) | — | 1.000 (1) | — | 1.000 (2) |
| Hindi (Devanagari) | 1.000 (2) | 1.000 (2) | 1.000 (1) | — | 1.000 (1) | 1.000 (2) | — |
| Hindi (romanised) | 1.000 (2) | 1.000 (5) | 1.000 (1) | — | 1.000 (2) | — | — |
| Bengali | 1.000 (2) | 1.000 (5) | 1.000 (1) | 1.000 (1) | 1.000 (1) | 1.000 (1) | — |

Exact-level recall per category per language (all cases in brackets):

| language | Suicidal ideation | Intent / plan | Self-harm | Severe hopelessness | Acute medical | Abuse disclosure | Harm to others |
| --- | --- | --- | --- | --- | --- | --- | --- |
| English | 0.833 (6) | 0.333 (12) | 1.000 (2) | 0.000 (6) | 0.000 (1) | — | 1.000 (2) |
| Hindi (Devanagari) | 0.667 (3) | 0.000 (2) | 0.500 (2) | 0.000 (1) | 0.000 (1) | 1.000 (2) | — |
| Hindi (romanised) | 0.667 (3) | 0.800 (5) | 0.500 (2) | 0.000 (3) | 0.000 (2) | — | — |
| Bengali | 1.000 (2) | 0.200 (5) | 0.500 (2) | 0.333 (3) | 0.000 (1) | 1.000 (1) | — |

Lowest-performing category per language (by exact-level recall):

- English: Severe hopelessness — exact-level recall 0.000 over 6 cases (crisis recall within it: 1.000)
- Hindi (Devanagari): Intent / plan — exact-level recall 0.000 over 2 cases (crisis recall within it: 1.000)
- Hindi (romanised): Severe hopelessness — exact-level recall 0.000 over 3 cases (crisis recall within it: 1.000)
- Bengali: Acute medical — exact-level recall 0.000 over 1 case (crisis recall within it: 1.000)

## Abstention policy (the trained model's own uncertainty)

Not applicable: this run's ML classifier does not expose a prediction
set, so there is no abstention to measure. (The Day-9 TF-IDF baseline
returns a point level only.)

## Cost of the recall target: benign escalations

- Benign cases (NONE/LOW): 51
- ...handed a crisis card (HIGH/IMMINENT): 31
- ...softened to a MEDIUM check-in: 7

The ensemble can raise but never lower, so these are the price paid for the
recall above at this dataset size; see the design doc for the trade-off and
the precision-leaning thresholds.

## False negatives at HIGH+IMMINENT (rules + ML ensemble)

None — every true HIGH/IMMINENT case was predicted HIGH or IMMINENT.

---
Generated by `evals/run_safety_eval.py` on 2026-10-20. Synthetic eval data only;
no real user text appears anywhere in this report.
