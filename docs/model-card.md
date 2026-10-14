# Model card — Manovia

| | |
| --- | --- |
| Card version | 1.2 (Day 33, 2026-10-14) — adds the trained safety classifier (C2b alternate) behind the unchanged ensemble, and states plainly that no trained number exists yet |
| Product | Manovia, a privacy-first mental wellbeing **self-help** companion |
| Status | Pre-launch. **Not clinically reviewed.** Clinical review is a precondition of public launch ([docs/clinical-review.md](clinical-review.md)). |
| Not | A medical device, a diagnostic tool, therapy, or a crisis service. It cannot send help to anyone. |
| Owner | The repository owner (see [CONTRIBUTING.md](../CONTRIBUTING.md)). Model changes follow the update policy in §10. |

This card describes every model and model-like component that decides or shapes
what a person sees: the crisis gate, the safety classifier, the emotion analyser,
the language-model providers, the output filter and the retrieval embedder. For
each one it says what it is, what it is for, how it was checked, and where it
is known to fail. The numbers below are copied from the evaluation reports in
[evals/reports/](../evals/reports/); nothing here is a number we could not trace.

Architecture context: [docs/architecture.md](architecture.md). Safety design:
[docs/safety-design.md](safety-design.md). Full evaluation method:
[docs/evaluation-report.md](evaluation-report.md).

## 1. Components at a glance

| # | Component | What it is | Role | Can it lower a safety level? | Where it runs | Default state |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | **Crisis rules engine** | Deterministic, pattern-based rules in YAML (`backend/app/content/safety/*.yaml`), with normalisation and pragmatics checks | Primary crisis detection. Runs **before any model call** | n/a (it is the floor) | In process, offline | On, always |
| C2 | **Safety ML classifier `crisis_v1`** | TF-IDF (character 2–4 and word 1–2 n-grams) into multinomial logistic regression, sigmoid-calibrated (cv = 3). Trained 2026-10-10 on the 340-case training split | A backstop for what the rules miss | **No.** It can only raise a level | In process, no network at load or predict time | On (`SAFETY_ML_ENABLED`) |
| C2b | **Safety ML classifier (trained, Day 33 alternate)** | `SAFETY_ML=trained`: a LoRA fine-tune of `FacebookAI/xlm-roberta-base` (MIT) into the five risk levels, trained with a cost-weighted loss, temperature-scaled on a dedicated calibration split, and given a conformal prediction set with an abstention policy ([ADR 0033](adr/0033-safety-trained-classifier.md)). **Not trained yet in this repository** — no GPU and no hub access, so no weights exist to point at | A backstop for what the rules miss, with a stated cost of each error and a stated uncertainty | **No.** It can only raise a level | In process, no network at load or predict time; a missing or unloadable artefact falls back to C2 with a `+trained-unavailable` version, never to rules-only | `SAFETY_ML=baseline` |
| C3 | **Ensemble** | Final level = max(rules level, ML level when confident), with a pragmatics gate. Code: `backend/app/services/safety/ensemble.py` | Combines C1 and C2 | **No** (by construction; partly tested; the safety branch gate for this module was closed on Day 28 after two tests were added, see [ADR 0029](adr/0029-branch-coverage-gates.md)) | In process | On |
| C4 | **Emotion analyser** | Primary by default: `SamLowe/roberta-base-go_emotions` through Hugging Face, **only when the `nlp` extra is installed**. Otherwise the lexicon chain (keyword, then sentiment). **Optional alternate (`EMOTION_ANALYZER=trained`, Day 32):** a locally fine-tuned XLM-R + LoRA model packaged as an artefact directory ([ADR 0032](adr/0032-emotion-lora-finetune.md)); never committed, and a missing directory falls back to the lexicon | A tone hint and an optional mood tag. It never decides risk | No | In process (`hf`/`trained` load a model on first use) | `EMOTION_ANALYZER=auto`; Compose sets `keyword`; `trained` only where a run artefact has been placed |
| C5 | **Lexicon fallbacks** | Keyword lexicon and a polarity sentiment scorer, `backend/app/services/nlp/` | Fallback for C4, and the path measured by the emotion evaluation | No | In process | Always attached |
| C6 | **Language-model provider** | One of: `anthropic` (hosted, `anthropic` extra), `ollama` (local), `fake` (scripted, for tests and development), plus the canned local reply as the last link of the chain | Writes replies for NONE, LOW and MEDIUM turns. **Never called at HIGH or IMMINENT** | No. Its output must pass C7 | Anthropic: provider's cloud. Ollama: your own host | `LLM_PROVIDER` must be set; config default is `fake` |
| C7 | **Output guard** | Deterministic checks on the whole reply: YAML categories in `backend/app/content/safety/output_patterns.yaml` (diagnosis, medication, identity claims, isolation or dependence, outcome promises, self-harm method language), plus length, language and question-count checks in `backend/app/services/safety/output_guard.py`. One strict regeneration, then a safe local reply | Blocks the categories above and replies in the wrong language or too long | It is a filter, so it can only block | In process | On |
| C8 | **Retrieval embedder** | Offline TF-IDF (default), or `paraphrase-multilingual-MiniLM-L12-v2` through sentence-transformers with the `rag` extra | Picks curated self-help passages for a reply | No | In process | TF-IDF unless the `rag` extra is installed |
| C9 | **Optional name redaction** | spaCy named-entity recognition through the `ner` extra | Removes person names from text sent to an LLM | n/a | In process | **Off** (`LLM_REDACT_NAMES=false`) |
| C10 | **Screening instruments** | PHQ-9 and GAD-7: official item wording and published scoring. Not machine learning | Self-screening, with support shown before any PHQ-9 score when item 9 is positive | n/a | Deterministic | On |

C1, C3, C7 and C10 are rules, not learned models, and carry most of the safety
weight. The learned components (C2, C2b, C4, C6, C8) are each bounded so that they
cannot lower a safety outcome, cannot be called at HIGH or IMMINENT risk, or are
checked by a deterministic filter before a person sees their output.

C2b is the same role as C2 behind the same interface and the same `combine()`.
What it adds is a **stated cost of each error** (the cost matrix in
[`ml/src/manovia_ml/training/safety/taxonomy.py`](../ml/src/manovia_ml/training/safety/taxonomy.py))
and a **stated uncertainty** (the conformal prediction set and the abstention
level), neither of which C2 has. What it does not add, today, is a number: no
trained model exists in this repository yet, and §5.2b says exactly that.

## 2. Intended use

- **Who it is for:** adults who want between-session self-help: reflecting on a
  day, naming a feeling, breathing and grounding exercises, a thought record, a
  mood check-in, a self-screen with PHQ-9 or GAD-7, and a short plan for the day.
- **Languages:** English (`en`), Hindi (`hi`, Devanagari, and romanised `hi-Latn`)
  and Bengali (`bn`). Hindi and Bengali copy is **not yet reviewed by native
  speakers** ([translations/STATUS.md](../translations/STATUS.md)).
- **Regions with helpline data:** IN, US, GB, AU, CA, plus a `DEFAULT` set of
  international numbers ([backend/app/content/helplines.json](../backend/app/content/helplines.json)).
- **Purpose of the safety layer:** to notice a risk and point the person to a
  human service, quickly and without a model in the loop. It is a signpost, not
  a treatment.

## 3. Out-of-scope use

Do not use Manovia, or any part of this model card's components, for:

- diagnosis, screening decisions by clinicians, or any clinical determination;
- advice on medication, doses or interactions;
- replacing therapy, psychiatric care or a crisis line, or as the first thing a
  person in danger reaches for;
- emergency response: the app cannot call anyone and does not know where the
  person is;
- monitoring, profiling or reporting on **other people** (employers, schools,
  partners, insurers, parents);
- predicting suicide or self-harm risk for research, insurance or any other
  decision about a person;
- **minors**: no age policy or age gate is implemented (see §9);
- languages other than `en`, `hi`/`hi-Latn` and `bn`, where the crisis rules and
  the classifier were not tested;
- training a new model on conversation data from users. No training script in this
  repository reads user data; the training and evaluation sets are synthetic (§4).

## 4. Training and evaluation data

No real user conversation was used to train or evaluate any component. Every
dataset is synthetic and written by the project team.

| Dataset | Size and split | Used for | Labelled by | Known bias |
| --- | --- | --- | --- | --- |
| `evals/datasets/crisis_cases.jsonl` (crisis) | 572 cases, split **train 340 / dev 116 / test 116**, seed `20261010`, SHA-256 in `manifest.json` | Training C2 (train). Threshold tuning (dev). Final check (test) | The project team, against a written scheme (`evals/build_crisis_dataset.py`) | Written by people who also wrote the rules and the patterns, so it measures agreement with the team's own judgement. Four languages (en, hi, hi-Latn, bn). Hard-negative families cover figurative speech, news, media, third person and recovery stories |
| `evals/datasets/emotion_cases.jsonl` (emotion) | 162 cases: en 72, hi 45, bn 45. 123 `in_domain`, 39 `domain_shift`. Nine emotions, 18 items each across languages (5–8 per emotion-language cell) | Measuring C4/C5 | The project team, not clinicians | Explicit lexical cues make up 123 of 162 items; the 39 `domain_shift` items are where the lexicon fails |
| `evals/datasets/chat_rubric.jsonl` (response) | 60 scripted multi-turn scenarios: en 26, hi 18, bn 16 (17 crisis) | Checking the whole chat pipeline with the fake provider | The project team, against six deterministic checks | Scripted, so it tests wiring and guards, not a live model's fluency |
| GoEmotions (for C4) | Google Research. Reddit comments, 27 emotions plus neutral | Pre-training of the checkpoint in C4. **We did not train it** | Third party | English Reddit only; **no source label maps to `loneliness`** |
| Screening instruments (C10) | Official PHQ-9 and GAD-7 wording and published scoring | Presenting and scoring the screens | Instrument authors | Validated for their original populations; see §6 |
| LLM providers (C6) | Third-party models | Generating replies | Provider | Provider training data and policies are the provider's; Manovia does not control them |

Data controls, enforced by tests: no method or means of self-harm appears in any
crisis case (`tests/safety/test_eval_dataset.py`); the test split's SHA-256 must
match the manifest (`evals/run_safety_eval.py --split test` refuses otherwise);
and the training and tuning scripts must not name the test file
(`tests/safety/`).

## 5. Metrics

The headline numbers, with confidence intervals and the caveats that go with
them. The full tables are in [evals/reports/2026-10-13/summary.md](../evals/reports/2026-10-13/summary.md)
and [docs/evaluation-report.md](evaluation-report.md).

### 5.1 Safety (C1 + C2 + C3), crisis tiers HIGH and IMMINENT

Positive class: a crisis tier (HIGH or IMMINENT). Recall is the share of crisis
cases that get the crisis response, the outcome the safety design protects.

| Split | Pipeline | Recall | Precision | Crisis cases | Interval |
| --- | --- | --- | --- | --- | --- |
| dev (used for tuning) | rules + ML ensemble | **1.000** | 0.543 | 51 | bootstrap 95% CI [1.000, 1.000] for recall, [0.441, 0.645] for precision |
| dev | rules only | 0.333 | 0.810 | 51 | recall [0.204, 0.471] |
| **test (frozen; scored on 2026-10-10 and 2026-10-12)** | rules + ML ensemble | **1.000** | 0.567 | 51 | see note |
| test | rules only | 0.255 | 0.812 | 51 | — |

**Note on the interval.** 51 of 51 cases correct gives a bootstrap interval of
[1.000, 1.000], which says nothing about the uncertainty. The exact (Clopper–Pearson)
95% interval for 51 of 51 is **[0.930, 1.000]**. Report that one. With 51 cases,
the true recall could be as low as about 0.93.

**Note on the split.** The threshold values (`min_confidence = 0.70`,
`crisis_mass_floor = 0.30`, `suspicion_floor = 0.25`) were chosen on dev. The
dev recall of 1.000 therefore overstates what the pipeline will do on new cases.
The test split is the honest number, but it has been opened more than once
(2026-10-10 and 2026-10-12), so it is no longer untouched.

Tier-level detail: `IMMINENT` is the tier that requires a time marker or a
means. On the **test** split the ensemble labels 3 of 20 IMMINENT cases as
IMMINENT (recall 0.150); on dev the exact `IMMINENT` recall is 0.100 (2 of 20).
Both tiers get the same policy (LLM blocked, crisis card, helplines and emergency
number), so the user-facing outcome is the same, but the tier label is weak.

### 5.2 The classifier on its own (C2)

On dev: accuracy **0.543**, macro-F1 **0.459**, HIGH+IMMINENT recall **0.804**
(metadata: [crisis_v1/metadata.json](../backend/app/ml_artifacts/crisis_v1/metadata.json)).
Alone, the classifier would miss about one crisis in five. This is why the
ensemble keeps the rules as the floor and uses the classifier only to raise a level.

### 5.2b The trained safety classifier (C2b, Day 33)

**No quality number is claimed here, and that is deliberate rather than coy.**
The sandbox that built the pipeline has no GPU and cannot reach the Hugging Face
hub, so `FacebookAI/xlm-roberta-base` cannot be downloaded and no trained weights
exist in this repository. The real run is an owner step:
[`ml/notebooks/colab_safety_lora.ipynb`](../ml/notebooks/colab_safety_lora.ipynb)
with [`ml/configs/safety_lora_xlmr.yaml`](../ml/configs/safety_lora_xlmr.yaml).

What *was* verified, on a randomly-initialised 2-layer XLM-R stand-in and 20
hand-written rows per split (`configs/safety_lora_smoke.yaml`, `make safety-smoke`):

| claim | what was checked | what it does not mean |
| --- | --- | --- |
| the cost-weighted loss reaches the optimiser | the matrix is logged with the run; `class_weights_from_cost` and the expected-cost decision rule are unit-tested against the matrix | nothing about how well the resulting model orders errors |
| temperature scaling is fitted, not assumed | the fitted `T` is recorded per run, ECE before/after and the reliability bins are in the artefact | the smoke run's `T ≈ 8–10` is a random model's number |
| conformal sets are computed and the policy is applied | the quantile, the coverage on the calibration split and on the held-out slice, and the abstention rate are all reported | the random model abstains on every row, so the policy's *cost* is unmeasured |
| the abstention policy never lowers a level | a property test over all five point levels × five set shapes, in both `ml/` and `backend/` | nothing about recall |
| the held-out split stays held out | every split's sha256 is in the manifest and in MLflow; the trainer refuses a corpus with a verbatim training row in the test split; the toy fixture is tested against the frozen split for near-duplicates (worst Jaccard 0.342) | the corpus itself contains near-paraphrases *within* the Day-9 hand-written set (max 0.719 to the frozen test split, zero verbatim overlap) |
| a trained model cannot silently become the baseline | `SAFETY_ML=trained` with a missing artefact yields a `+trained-unavailable` version, and the eval harness refuses to write a report that says `trained` without saying whether it loaded | nothing about the trained model's quality |

The corpus behind it: 456 hand-written Day-9 seeds expanded to **3,445 kept rows**
(127 dropped as `rules_inconsistent`), split train 2,756 / dev 365 / calibration
358. It is **synthetic and templated**, in four registers written by one person,
with no native-speaker review — so a number measured on it is a number about it.
The full statement of what this work does not establish is
[`docs/safety-design.md` §12.9](safety-design.md).

### 5.3 Emotion (C4 as shipped: the lexicon chain)

Evaluated on the offline chain that runs when the HF checkpoint is absent
(`keyword` then `sentiment`), not on the RoBERTa checkpoint.

| Measure | Value | 95% bootstrap CI |
| --- | --- | --- |
| Macro-F1, all 162 cases | **0.804** | [0.739, 0.853] |
| Accuracy | 0.765 | [0.698, 0.827] |
| Macro-F1, `in_domain` (123) | 0.992 | — |
| Macro-F1, `domain_shift` (39) | **0.011** | — |
| Macro-F1 per language (en / hi / bn) | 0.802 / 0.833 / 0.774 | — |

The overall 0.804 hides a collapse: on indirect, somatic or idiomatic wording the
analyser is wrong in 37 of 39 cases. In the report's listed examples, 11 of the 14 wrong predictions are `neutral`.

**The RoBERTa checkpoint (C4 primary) has not been measured on this dataset.**
Its accuracy on our data is unknown. The gap is listed as item 3 in
`docs/evaluation-report.md` §4 and in PROGRESS.md.

### 5.3b The fine-tuned model (C4 alternate, Day 32)

`EMOTION_ANALYZER=trained` runs a model that *is* this repository's: LoRA on
`FacebookAI/xlm-roberta-base` (MIT), fine-tuned into Manovia's nine emotions
through a documented mapping table, with per-label thresholds tuned on the
validation split ([ADR 0032](adr/0032-emotion-lora-finetune.md);
[`ml/`](../ml/README.md)).

**No quality number is claimed here yet**, and that is deliberate. The sandbox
that built it has no GPU and cannot reach the Hugging Face hub, so the real run
(the model, GoEmotions, the hand-written 174-row en/hi/bn held-out set) is an
owner step: `ml/notebooks/colab_emotion_lora.ipynb`. What was verified here is
the machinery — the same code path ran end to end on a tiny random-initialised
model (`configs/emotion_lora_smoke.yaml`), producing a packaged artefact, an
MLflow run, per-language metrics with bootstrap intervals, and a lexicon
fallback when the artefact is deleted. **A smoke run's metrics are noise by
construction and are not quoted as performance.**

What the card will have to say when the real numbers exist is already decided,
because the pipeline prints it rather than requiring anyone to remember:

- the comparison is three-way and per language (lexicon chain, Day-6
  off-the-shelf checkpoint, fine-tuned), with 95% bootstrap intervals, and the
  report emits an explicit sentence for every language where a baseline wins;
- **Hindi and Bengali are transfer, not supervision** — GoEmotions is English,
  so a hi/bn number from this run measures how the multilingual encoder carries
  English supervision, not a Hindi-trained model;
- **`loneliness` has no GoEmotions supervision at all**, and the run's manifest
  records it (`labels_unreachable_from_training_data`);
- the tuned thresholds live in the artefact's `thresholds.json` with the split
  they were tuned on, and the evaluation never tunes on the held-out set;
- the run's own `MODEL_CARD.md` — written from
  [`ml/templates/MODEL_CARD.md`](../ml/templates/MODEL_CARD.md) — always carries
  the next limitation, the **domain shift from social-media text**, quoted
  verbatim below, before any number is quoted from it.

> **Domain shift from social-media text.** GoEmotions is Reddit comments written
> by self-selected, mostly English-speaking internet users. The wording people
> bring to a wellbeing companion is not Reddit's wording: it is shorter, often
> about ordinary days, and frequently in Hindi or Bengali. Every number in this
> card comes from a dataset drawn from that social-media distribution, so a
> model can look strong here and still be wrong on the text it actually
> receives — the Day-27 `domain_shift` slice (macro-F1 0.011 for the lexicon
> chain) is the same failure at a different scale.

### 5.4 Response pipeline (C6, C7, orchestrator), with the fake provider

60 of 60 scenarios pass all six deterministic checks: no diagnosis or medication
language, length within bounds, language matches, crisis resources when required,
at most one question, no AI boilerplate. Crisis scenarios (17 of 17) make no LLM
call. This proves the **wiring and the guards**. It does not prove what a live
model would say.

The LLM-as-judge rubric (`evals/prompts/response_judge_v1.md`) is opt-in and
advisory. Its offline scores (5.00 / 5) are not evidence of quality.

## 6. Screening instruments (Day 20)

The instruments are reproduced exactly as published. The band copy, the phrase
"screening result, not a diagnosis" and everything Manovia says about a score are
written by the project and have not been reviewed by a clinician (see §9).

Both instruments are reproduced **unaltered** for a person's own self-help
screening. Manovia does not modify them, does not relicense them, and is not
affiliated with, endorsed by or sponsored by Pfizer Inc. or the instruments'
developers.

### 6.1 PHQ-9 — Patient Health Questionnaire-9

| | |
| --- | --- |
| Developers | Drs. Robert L. Spitzer, Janet B.W. Williams, Kurt Kroenke and colleagues, with an educational grant from Pfizer Inc. |
| Rights holder | Copyright © 1999 Pfizer Inc. All rights reserved. PRIME-MD® is a trademark of Pfizer Inc. |
| Licence / availability | **"No permission required to reproduce, translate, display or distribute."** The official English sheet is published free of charge for clinical, research and educational use. |
| Source (item wording) | <https://www.phqscreeners.com/images/sites/g/files/g10060481/f/201412/PHQ-9_English.pdf> |
| Source (scoring) | <https://www.phqscreeners.com/images/sites/g/files/g10016261/f/201412/instructions.pdf> — "Scores of 5, 10, 15, and 20 represent cutpoints for mild, moderate, moderately severe and severe depression, respectively." |
| Items / range | 9 items, each 0–3; total 0–27 |
| Bands | 0–4 minimal · 5–9 mild · 10–14 moderate · 15–19 moderately severe · 20–27 severe |
| Safety rule | Item 9 (thoughts of being better off dead or of hurting yourself) answered **above zero** triggers the crisis flow regardless of the total. |
| LOINC | `44261-6` — Patient Health Questionnaire 9 item (PHQ-9) total score [Reported] |
| Citation | Kroenke K, Spitzer RL, Williams JB. *The PHQ-9: validity of a brief depression severity measure.* J Gen Intern Med. 2001;16(9):606–613. |
| Wording verified | 2026-10-11, character by character, against the official PDF above. |

### 6.2 GAD-7 — Generalized Anxiety Disorder-7

| | |
| --- | --- |
| Developers | Drs. Robert L. Spitzer, Janet B.W. Williams, Kurt Kroenke and colleagues, with an educational grant from Pfizer Inc. |
| Rights holder | Copyright © Pfizer Inc. All rights reserved. |
| Licence / availability | **"No permission required to reproduce, translate, display or distribute."** The official English sheet is published free of charge for clinical, research and educational use. |
| Source (item wording) | <https://www.phqscreeners.com/images/sites/g/files/g10060481/f/201412/GAD-7_English.pdf> |
| Source (scoring) | <https://www.phqscreeners.com/images/sites/g/files/g10016261/f/201412/instructions.pdf> — "Scores of 5, 10, and 15 represent cutpoints for mild, moderate, and severe anxiety, respectively." |
| Items / range | 7 items, each 0–3; total 0–21 |
| Bands | 0–4 minimal · 5–9 mild · 10–14 moderate · 15–21 severe |
| Safety rule | No suicidality item; no crisis path. |
| LOINC | `69737-5` — Generalized anxiety disorder 7 item (GAD-7) panel · `70274-6` — GAD-7 total score [Reported.PHQ] |
| Citation | Spitzer RL, Kroenke K, Williams JB, Löwe B. *A brief measure for assessing generalized anxiety disorder: the GAD-7.* Arch Intern Med. 2006;166(10):1092–1097. |
| Wording verified | 2026-10-11, character by character, against the official PDF above. |

The machine-readable form of all of the above — including the availability note,
the exact scoring-source quotes and the non-affiliation disclaimer — is the
`provenance` block of `backend/app/content/assessments/phq9.json` and
`gad7.json`, which the API publishes and both the result page and the printable
summary display.

**Band copy and the phrase "screening result, not a diagnosis" are written by
this project, not by the instruments' authors**, and have not been reviewed by a
clinician. The instrument contributes the items, the response set, the scoring
rule and the cutpoints; everything Manovia says *about* a score is Manovia's.

## 7. Known failure modes

Each failure below is measured or traced to a file. None is hidden by a headline number.

| # | Failure | Evidence | Who is affected, and how | Mitigation in place | Still open |
| --- | --- | --- | --- | --- | --- |
| F1 | **Over-escalation.** Benign or past-tense distress is labelled HIGH: recovery stories, third-person worry, media and news that share crisis vocabulary, everyday exam stress | Dev: 31 of 51 non-crisis cases escalated. Precision 0.543. Test: precision 0.567 | A person who is struggling but safe receives a crisis card instead of a conversation. The safety design accepts this cost on purpose; the cost is real | Pragmatics gate (negation and figurative frames cannot be re-raised by the model); hard-negative families in the dataset | Yes. A retrain with more everyday hard negatives is parked (PROGRESS.md) |
| F2 | **Missed crisis without the usual words.** A crisis written with none of the obvious terms | Test split: recall 1.000 with the ensemble, but 0.255 with rules alone, so the classifier carries the indirect cases. Standalone classifier recall 0.804 | A person whose wording is unusual may get no crisis reply, and then a normal LLM reply | ML backstop; output guard; the design never claims a guarantee | Yes. Only 51 dev and 51 test crisis cases, all synthetic |
| F3 | **Tier confusion inside crisis.** HIGH and IMMINENT are often mixed | Dev: exact IMMINENT recall 0.100. Test: 0.150 | The tier label and the card wording differ, but the policy is the same, so the user outcome is largely the same | Both tiers block the LLM, show the same helplines and the emergency number | Yes |
| F4 | **Emotion domain shift.** Somatic, idiomatic, sarcastic or behavioural wording is missed | `domain_shift` macro-F1 0.011 (37 of 39 wrong) | Tone and mood-tag suggestions are wrong. A check-in tag can be the wrong one | Emotion is a tone hint, never a risk input | Yes. The RoBERTa checkpoint is unmeasured here, and fine-tuning changes the label space, not the domain (F13) |
| F5 | **`loneliness` is not produced by the checkpoint.** No GoEmotions label maps to it | Evaluation report §4 item 3 | Loneliness is under-detected by the model path | The lexicon path can produce it, but only for explicit words | Yes |
| F6 | **Languages.** The Hindi and Bengali crisis copy is AI-drafted and **not reviewed** by native speakers. Romanised Bengali is only partly covered: the emotion set's `bn` slice includes romanised Banglish, but there is no `bn-Latn` tag | `translations/STATUS.md` (every `hi` and `bn` key is marked as an unreviewed draft); `evals/datasets/README.md` | Users of these languages may get wording that is wrong or unnatural in a crisis | Crisis copy is pre-written, not generated at run time, so the risk is wording, not improvisation | Yes. A launch blocker (clinical review, items A-1 and A-2) |
| F7 | **Helpline entries that could not be fully confirmed.** Four entries carry `needs_verification: true`: iCall, KIRAN and Aasra (IN), and Kids Helpline (AU) | `backend/app/content/helplines.json`, `last_verified 2026-10-09` | A person may be given a number that is out of service, the wrong hours, or the wrong language | Every entry has a `source_url`; unverified entries say why | Yes. A launch blocker (clinical review, item H-1) |
| F8 | **Provider drift.** The live LLM can change under us, and the repository does not record which model version is in use | Config reads `ANTHROPIC_MODEL` from the environment and does not pin one | Reply quality and tone can change between deployments without a code change | Output guard runs on every reply; the canned fallback is used when the provider fails | Yes. Record the model id in the deployment record |
| F9 | **Output guard coverage.** The guard is rules-based, so it catches the categories it knows and nothing else | `output_patterns.yaml`; the six checks in §5.4 | A subtle harmful reply that matches no pattern gets through | One strict regeneration; a safe fallback; the response evaluation | Yes |
| F10 | **Single-message view.** Risk is judged per message. Saved chats carry a bounded summary, and ephemeral chats carry no memory | `docs/architecture.md`, "Where a conversation lives" | A risk that builds slowly over many turns can be judged low at each step | Memory summaries are in the prompt as untrusted context; the crisis check runs on every message | Yes. Long-horizon evaluation is parked |
| F11 | **Model library drift.** The classifier was trained on scikit-learn 1.9.1 but the environment has warned on version mismatch | PROGRESS.md "Known issues" | A retrain or a library change could move thresholds silently | Metadata records the hashes and the training split; thresholds are gated by `make eval` | Yes |
| F13 | **Domain shift from social-media text.** The fine-tuned model (C4 alternate) is trained on GoEmotions, i.e. Reddit comments | [ADR 0032](adr/0032-emotion-lora-finetune.md) §2; `ml/data/CATALOGUE.md` § GoEmotions; the card the run writes always contains this sentence | Every number for the fine-tuned model comes from that distribution. Ordinary, short, everyday text — and Hindi/Bengali text — is out of distribution, so a good score here does not transfer by itself | Domain-shift items are separate in the Day-6 evaluation set; the held-out hi/bn rows are hand-written, not scraped, and the report names the languages where a baseline wins | Yes. Waiting on the natively reviewed hi/bn corpus (`ml/data/planned/hi_bn_emotion/`) |
| F12 | **Code-mixed language.** Hinglish and Banglish mix scripts and spellings, and the emotion lexicon was not measured on them separately | `evals/datasets/README.md` (emotion `hi` and `bn` slices include romanised text); `evals/reports/2026-10-13/emotion.md` reports per-language F1 only | Emotion tags on code-mixed text may be wrong | Crisis recall is gated per language, including `hi-Latn`, at 0.950 or above (`evals/thresholds.yaml`). Emotion is a tone hint, not a risk input | Yes. Each emotion-language cell has only 5–8 items (27 cells), too few for per-cell error rates |

## 8. Ethical considerations

- **Not a substitute for care.** The product says so in the app, in the README and in
  the crisis card. The design assumes a person who wants help can reach a human,
  and it shows a helpline first when risk is high.
- **Consent and honesty.** The app discloses that it is an AI, does not claim to be a
  therapist or a human, and asks for consent before storing conversations (AGENTS.md
  rule 3; ADR 0004).
- **Privacy by design.** Journal, mood notes and saved chats are encrypted at rest.
  Logs carry no message text (`backend/tests/security/test_log_sentinels.py`). Identifiers
  are never sent to a provider (`backend/tests/llm/test_no_pii_reaches_provider.py`).
  The threat model is [docs/privacy-and-threat-model.md](privacy-and-threat-model.md).
- **Surveillance risk.** The same signals that detect crisis could be used to watch
  other people. The product is designed for the person's own use. Out-of-scope use in §3
  forbids using it on others.
- **Harm from false alarms.** A crisis card shown to someone who is safe is a harm: it
  can make them stop being honest. The design accepts over-escalation (F1) and records
  the cost in §5.1, not just the benefit.
- **Harm from silence.** A missed crisis is the worst outcome. The design fails closed:
  if the safety stage errors, the reply is a 503, never an unchecked answer (ADR 0008,
  `backend/app/services/chat/orchestrator.py`).
- **Cultural and language fit.** Helplines and copy were chosen for India, the US, the
  UK, Australia and Canada. Most of the team's testing is English. Other regions get a
  `DEFAULT` set. This is a fairness gap, not a solved problem.
- **Accessibility.** The interface targets WCAG 2.2 AA (ADR 0025). Accessibility claims
  are supported by automated checks (axe, keyboard and zoom tests), not by an audit by
  disabled users.
- **Labour and provenance.** Evaluation labels were written by the team, not by
  clinicians or crowdworkers. That makes the evaluation a consistency check, not an
  independent measure of clinical accuracy.

## 9. Open questions a reviewer should challenge

These are the points an expert reviewer is likely to push on. Several are launch
blockers in [docs/clinical-review.md](clinical-review.md).

1. **Are the crisis thresholds right for the population?** They were chosen on the dev
   split, and the 1.000 recall is therefore optimistic. On dev, about 46% of escalations
   (1 − precision 0.543) were not crises. How much false alarm is acceptable is a
   clinical judgement, not an engineering one.
2. **Is 51 of 51 enough?** The exact interval is [0.930, 1.000]. A larger, independently
   labelled set is needed before any recall figure is quoted externally.
3. **Who labelled the data?** The team did, and the team also wrote the rules. Independent
   labelling by clinicians and native speakers has not been done.
4. **Is the emotion analyser fit for anything?** As shipped, it is a lexicon, and it fails
   on indirect wording. Its only use is a tone hint and an optional tag. Should it exist at
   all in the default build?
5. **Which provider model is live, and was it evaluated?** The live model is not recorded
   in this repository, and the evaluation uses a fake provider.
6. **What happens for minors?** No age policy or age gate is implemented. Crisis handling
   for under-18s may need different resources and different consent.
7. **Are the Hindi and Bengali crisis replies safe and natural?** They are AI-drafted and
   not reviewed.
8. **Does the PHQ-9 presentation suit a self-help product?** The instrument is reproduced
   unaltered, but the context in which a score appears is Manovia's, and it has not been
   clinically reviewed.
9. **Is the update policy enforced or only written down?** See §10. Parts are enforced by
   tests. Others depend on people following the process.
10. **Does the fine-tuned model (C4 alternate) beat the lexicon on the languages
    people actually write in?** The three-way comparison is code, but the run
    needs a GPU and the GoEmotions download, so the answer is still open —
    and "it beats the keyword chain on the held-out set" would still not answer
    the Hindi question, which needs native-reviewed rows.

## 10. Update policy

**What is enforced by the repository today:**

- Thresholds are declared in `evals/thresholds.yaml`. `make eval` fails the build if a
  gated number drops (`evals/run_all_evals.py`).
- Safety recall may not regress against the previous dated report
  (`evals/reports/`). A drop fails the gate.
- The frozen test split is hash-checked against `evals/datasets/manifest.json`.
  Tuning scripts are tested to not name it.
- The ML artifact records its training data hashes, parameters and dev metrics in
  `backend/app/ml_artifacts/crisis_v1/metadata.json`.
- Helpline data is validated against `helplines.schema.json` on every test run.

**What is policy, not yet enforced by code:**

1. Any change to a threshold, a crisis pattern, the classifier, or a crisis reply needs
   an ADR and a clinical reviewer's sign-off recorded in the pull request.
2. Any new model or model version gets a row in §1, a line in the evaluation report, and
   a `CHANGELOG.md` entry.
3. The test split is not used for tuning. When it is opened again, the card records the
   date.
4. The live provider model id is recorded per deployment.
5. This card is reviewed before each public release, and at least every three months while
   the product is live. This interval is a proposed commitment, and no named owner has
   accepted it yet.

**Versioning.** This card is version 1.0. A change to any row in §1, to a number in §5,
or to the launch status increments the minor version. The date in the header changes with it.

## 11. Feedback

Report a wrong number, a missed case, or a helpline problem through the issue forms
(`.github/ISSUE_TEMPLATE/safety_concern.yml`). Report anything that could be used to
bypass crisis handling privately, as described in [SECURITY.md](../SECURITY.md).

## 12. Manovia's own code and licence

The repository is MIT-licensed (see `LICENSE`). The scoring rules, content files,
safety rules, copy and UI in this project are the project's. The instruments and
third-party models above are not, and keep their own terms.
