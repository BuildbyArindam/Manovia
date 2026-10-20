# Manovia Evaluation Harness Summary — 2026-10-20

- **Overall CI Gate Verdict**: **PASS** (thresholds from `evals/thresholds.yaml`)
- **Execution Mode**: Offline-capable deterministic + ensemble harness (`fake (FakeLLMProvider, 100% offline)`)
- **Suite Reports**: [`safety.md`](./safety.md) · [`emotion.md`](./emotion.md) · [`response.md`](./response.md) · [`report.html`](./report.html)

## 1. Headline metrics across all three evaluation suites

| suite | dataset / split | headline metric | value | 95% bootstrap CI | gate |
| --- | --- | --- | --- | --- | --- |
| **1. Safety (rules + ML ensemble)** | `crisis_cases_dev.jsonl` (n=116, crisis=51) | HIGH+IMMINENT recall | **1.000** | [1.000, 1.000] | **PASS** |
| **1b. Safety (precision trade-off)** | `crisis_cases_dev.jsonl` (n=116) | HIGH+IMMINENT precision | **0.543** | [0.441, 0.645] | info |
| **1c. Safety (rules-only baseline)** | `crisis_cases_dev.jsonl` (n=116) | HIGH+IMMINENT recall (rules) | **0.333** | [0.204, 0.471] | **PASS** |
| **2. Emotion analyzer (9-class)** | `emotion_cases.jsonl` (n=162, en/hi/bn) | Macro-F1 (overall) | **0.804** | [0.739, 0.853] | **PASS** |
| **2b. Emotion (in-domain vs shift)** | `in_domain` (123) vs `domain_shift` (39) | Macro-F1 (`in` -> `shift`) | **0.992 -> 0.011** | drop = 0.981 | documented |
| **3. Response pipeline (6 checks)** | `chat_rubric.jsonl` (n=60, en/hi/bn) | All-6 deterministic pass rate | **1.000** | [1.000, 1.000] | **PASS** |

## 2. Consolidated CI gate checks (`evals/thresholds.yaml`)

| suite | check | threshold | observed | verdict |
| --- | --- | --- | --- | --- |
| `safety` | HIGH+IMMINENT recall (active pipeline) | `>= 0.970` | `1.000` | **PASS** |
| `safety` | HIGH+IMMINENT recall (rules-only baseline) | `>= 0.330` | `0.333` | **PASS** |
| `safety` | Per-language HIGH+IMMINENT recall (min across en/hi/hi-Latn/bn) | `>= 0.950` | `1.000` | **PASS** |
| `safety` | No recall regression vs previous report | `delta >= 0.000` | `0 regressions` | **PASS** |
| `emotion` | Overall macro-F1 (9-class emotion chain) | `>= 0.550` | `0.804` | **PASS** |
| `emotion` | Per-language macro-F1 floor (en, hi, bn) | `>= 0.450` | `0.774 (bn)` | **PASS** |
| `response` | Overall deterministic scenario pass rate (all 6 checks) | `>= 1.000` | `1.000 (60/60)` | **PASS** |
| `response` | Crisis resource delivery & LLM bypass on crisis scenarios | `>= 1.000` | `1.000 (17/17)` | **PASS** |
| `response` | No diagnosis or medication terms rate | `>= 1.000` | `1.000` | **PASS** |
| `response` | No 'as an AI language model' boilerplate rate | `>= 1.000` | `1.000` | **PASS** |

## 3. Safety regression diff vs previous report

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
| Benign crisis escalations (count) | 31.000 | 31.000 | +0.000 | **UNCHANGED** |

## 4. Response pipeline deterministic check breakdown (n=60)

| check | passed | rate | 95% bootstrap CI |
| --- | --- | --- | --- |
| `no_diagnosis_or_medication` | 60/60 | **1.000** | [1.000, 1.000] |
| `within_length_limit` | 60/60 | **1.000** | [1.000, 1.000] |
| `language_match` | 60/60 | **1.000** | [1.000, 1.000] |
| `crisis_resources_when_required` | 60/60 | **1.000** | [1.000, 1.000] |
| `at_most_one_question` | 60/60 | **1.000** | [1.000, 1.000] |
| `no_ai_boilerplate` | 60/60 | **1.000** | [1.000, 1.000] |

- **Optional LLM-as-judge rubric (`evals/prompts/response_judge_v1.md`)**: disabled (enable with `--with-judge`)

## 5. What these numbers do and do not mean (summary)

- **What they DO mean**: Every authored crisis pattern and ML ensemble threshold in the repository is regression-tested against a 116-item dev split with bootstrap confidence intervals; weakening a `HIGH` pattern in `patterns_core.yaml` or `patterns_indic.yaml` immediately trips the CI gate. Every one of the 60 scripted `ChatOrchestrator` scenarios satisfies all 6 deterministic output safety invariants across English, Hindi, and Bengali.
- **What they DO NOT mean**: A `1.000` recall on 51 synthetic dev crisis cases is **not** a clinical guarantee of zero false negatives in the wild, and a `0.804` overall emotion macro-F1 masks a severe drop on implicit/somatic domain-shift expressions (`0.992` in-domain vs `0.011` domain-shift). See `docs/evaluation-report.md` for the full analysis.
