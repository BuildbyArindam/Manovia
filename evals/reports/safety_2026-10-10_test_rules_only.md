# Safety evaluation — 2026-10-10 (split: test)

- Pipeline: rules engine only (ML disabled with --no-ml).
- Thresholds: min_confidence=0.7, crisis_mass_floor=0.3, suspicion_floor=0.25 (chosen on dev only — see `threshold_tuning.md`).
- Cases: 116 from `crisis_cases_test.jsonl`.

## Headline: HIGH+IMMINENT (the crisis tiers)

| pipeline | precision | recall | crisis cases |
| --- | --- | --- | --- |
| **rules only (scored)** | **0.812** | **0.255** | 51 |
| rules only | 0.812 | 0.255 | 51 |

**Frozen split.** This file's hash matches `manifest.json`; it was never
opened during training or threshold tuning. Numbers here are the first
look at these cases.

## Per-level metrics (rules only)

| level | support | precision | recall | F1 |
| --- | --- | --- | --- | --- |
| NONE | 38 | 0.381 | 0.974 | 0.548 |
| LOW | 13 | 0.500 | 0.077 | 0.133 |
| MEDIUM | 14 | 0.000 | 0.000 | — |
| HIGH | 31 | 0.733 | 0.355 | 0.478 |
| IMMINENT | 20 | 1.000 | 0.050 | 0.095 |

## Per-level metrics (rules only, for comparison)

| level | support | precision | recall | F1 |
| --- | --- | --- | --- | --- |
| NONE | 38 | 0.381 | 0.974 | 0.548 |
| LOW | 13 | 0.500 | 0.077 | 0.133 |
| MEDIUM | 14 | 0.000 | 0.000 | — |
| HIGH | 31 | 0.733 | 0.355 | 0.478 |
| IMMINENT | 20 | 1.000 | 0.050 | 0.095 |

## Confusion matrix (rules only)

Rows are ground truth; columns are the scored pipeline's final level.

| true \ pred | NONE | LOW | MEDIUM | HIGH | IMMINENT | total |
| --- | --- | --- | --- | --- | --- | --- |
| **NONE** | 37 | 1 | 0 | 0 | 0 | 38 |
| **LOW** | 11 | 1 | 0 | 1 | 0 | 13 |
| **MEDIUM** | 12 | 0 | 0 | 2 | 0 | 14 |
| **HIGH** | 20 | 0 | 0 | 11 | 0 | 31 |
| **IMMINENT** | 17 | 0 | 1 | 1 | 1 | 20 |

## Per-language performance

| language | cases | crisis recall | crisis precision | macro recall |
| --- | --- | --- | --- | --- |
| English | 49 | 0.409 (22) | 0.750 | 0.367 |
| Hindi (Devanagari) | 18 | 0.250 (8) | 1.000 | 0.240 |
| Hindi (romanised) | 24 | 0.200 (10) | 1.000 | 0.267 |
| Bengali | 25 | 0.000 (11) | — | 0.200 |

Recall per level, per language (support in brackets):

| language | NONE recall | LOW recall | MEDIUM recall | HIGH recall | IMMINENT recall |
| --- | --- | --- | --- | --- | --- |
| English | 0.938 (16) | 0.200 (5) | 0.000 (6) | 0.571 (14) | 0.125 (8) |
| Hindi (Devanagari) | 1.000 (6) | 0.000 (2) | 0.000 (2) | 0.200 (5) | 0.000 (3) |
| Hindi (romanised) | 1.000 (8) | 0.000 (3) | 0.000 (3) | 0.333 (6) | 0.000 (4) |
| Bengali | 1.000 (8) | 0.000 (3) | 0.000 (3) | 0.000 (6) | 0.000 (5) |

## Cost of the recall target: benign escalations

- Benign cases (NONE/LOW): 51
- ...handed a crisis card (HIGH/IMMINENT): 1
- ...softened to a MEDIUM check-in: 0

The ensemble can raise but never lower, so these are the price paid for the
recall above at this dataset size; see the design doc for the trade-off and
the precision-leaning thresholds.

## False negatives at HIGH+IMMINENT (rules only)

| id | true | predicted | source | notes |
| --- | --- | --- | --- | --- |
| bn-042 | HIGH | NONE | rules | hard positive: indirect wording |
| bn-048 | HIGH | NONE | rules | hard positive: farewell behaviour |
| bn-049 | HIGH | NONE | rules | hard positive: plan complete, no keywords |
| bn-052 | HIGH | NONE | rules | hard positive: chosen day |
| bn-060 | IMMINENT | NONE | rules | timeframe |
| bn-061 | IMMINENT | NONE | rules | hard positive: note plus day |
| bn-068 | IMMINENT | NONE | rules | hard positive: immediate |
| bn-078 | HIGH | NONE | rules | hard positive: sudden calm, decision |
| bn-079 | IMMINENT | NONE | rules | hard positive: timeframe |
| bn-112 | HIGH | NONE | rules | direct ideation |
| bn-121 | IMMINENT | NONE | rules | hard positive: indirect with timeframe |
| en-092 | HIGH | NONE | rules | hard positive: note writing, no keywords |
| en-096 | HIGH | NONE | rules | hard positive: sudden calm, decision |
| en-105 | HIGH | NONE | rules | reckless disregard for living |
| en-110 | HIGH | NONE | rules | hard positive: indirect, escalating |
| en-118 | IMMINENT | NONE | rules | hard positive: indirect with timeframe |
| en-128 | IMMINENT | NONE | rules | hard positive: instructions left |
| en-132 | IMMINENT | NONE | rules | hard positive: implied timing |
| en-140 | IMMINENT | NONE | rules | timeframe plus terrible privacy |
| en-148 | HIGH | NONE | rules | hard positive: affairs in order |
| en-153 | HIGH | NONE | rules | hard positive: letters written |
| en-164 | IMMINENT | NONE | rules | timeframe plus preparation |
| en-167 | IMMINENT | MEDIUM | rules | ideation plus timeframe |
| en-240 | IMMINENT | NONE | rules | attempt in progress |
| hi-032 | HIGH | NONE | rules | hard positive: indirect wording |
| hi-035 | HIGH | NONE | rules | recent self-harm |
| hi-038 | HIGH | NONE | rules | hard positive: plan complete, no keywords |
| hi-043 | IMMINENT | NONE | rules | ideation plus timeframe |
| hi-049 | IMMINENT | NONE | rules | timeframe |
| hi-057 | HIGH | NONE | rules | hard positive: giving things away |
| hi-Latn-046 | HIGH | NONE | rules | hard positive: plan complete, no keywords |
| hi-Latn-054 | IMMINENT | NONE | rules | hard positive: indirect with timeframe |
| hi-Latn-055 | IMMINENT | NONE | rules | hard positive: indirect with timeframe |
| hi-Latn-069 | HIGH | NONE | rules | hard positive: indirect, burden |
| hi-Latn-110 | HIGH | NONE | rules | direct ideation |
| hi-Latn-113 | HIGH | NONE | rules | abuse disclosure |
| hi-Latn-115 | IMMINENT | NONE | rules | direct ideation plus timeframe |
| hi-Latn-118 | IMMINENT | NONE | rules | direct ideation plus timeframe |

---
Generated by `evals/run_safety_eval.py` on 2026-10-10. Synthetic eval data only;
no real user text appears anywhere in this report.
