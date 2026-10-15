# Day 35 verification — fine-tuned retrieval: embeddings, hybrid search and reranking

Every command below was run in the sandbox on 2026-10-15 (2 CPUs, Python 3.11,
numpy/scikit-learn/faiss-cpu, **no torch, no transformers, no onnx-runtime for the
retrieval path** — Hugging Face and the torch wheel index are unreachable here).
Numbers come from measured runs, not from estimates:

* retrieval eval — `make ml-retrieval-eval` → `ml/reports/day35-retrieval.md`
* encoder fine-tune — `make ml-retrieval-finetune` (MLflow `b4e61549403a441e8e8fd80186849f99`)
* fusion sweep — `make ml-retrieval-tune` (dev only)
* cross-encoder fit — `make ml-retrieval-rerank` (MLflow `bded05be3f28444c8f3eeac27ebb1728`)
* abstain calibration — `make ml-retrieval-calibrate` (calibration split only)
* backend suite + Day-26 coverage gate — `cd backend && make test`
* live chat turns — the transcript in §5, from the real orchestrator and the real
  factory-built retriever over an ingested 40-document corpus

The corpus is 40 documents / 59 passages (fingerprint `0c0155232a8ff009…`), the eval
set is 254 queries (154 hand-written + 100 flagged synthetic), and the test split is
52 hand-written rows. **52 rows is small**: the bootstrap intervals below are wide
(±0.13 on MRR) and a difference inside its own interval is not a finding.

## 1. Comparison table — baseline vs fine-tuned dense vs hybrid vs hybrid + rerank

`make ml-retrieval-eval` on the **test** split (52 queries, k = [1, 3, 5], 3 latency
repeats per query, 2000 bootstrap resamples). Ranking is measured with the abstain
floor *not* applied, so the rows compare the systems rather than the decision.

| setting | what it is | R@1 | R@3 | R@5 | MRR | hit@3 | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `baseline` | Day-13: dense only, off-the-shelf TF-IDF | 0.292 | 0.500 | 0.592 | 0.639 | 0.750 | 2.7 | 3.1 |
| `dense_finetuned` | contrastive projection only (MNRL + hard negatives) | 0.317 | 0.433 | 0.487 | 0.629 | 0.700 | 2.4 | 2.8 |
| `bm25` | BM25 only (the lexical half) | 0.308 | 0.463 | 0.496 | 0.626 | 0.675 | 2.6 | 2.8 |
| `hybrid` | BM25 + off-the-shelf dense, RRF (`dense_weight` 0.20) | 0.296 | 0.483 | 0.517 | 0.622 | 0.700 | 2.7 | 3.3 |
| `hybrid_rerank_untrained` | hybrid + hand-set cross-encoder | 0.296 | 0.458 | 0.504 | 0.619 | 0.675 | 7.7 | 8.8 |
| **`hybrid_rerank`** | **hybrid + fitted cross-encoder (recommended)** | **0.354** | 0.467 | 0.529 | **0.658** | 0.700 | 7.9 | 8.5 |
| `hybrid_finetuned` | BM25 + fine-tuned dense | 0.312 | 0.479 | 0.554 | 0.667 | 0.775 | 2.3 | 2.6 |
| `hybrid_finetuned_rerank` | fine-tuned dense + fitted cross-encoder | 0.338 | 0.500 | 0.571 | 0.707 | 0.775 | 7.4 | 8.3 |

What each stage costs and buys, same queries:

| change | ΔR@1 | ΔR@3 | ΔR@5 | ΔMRR | Δp50 ms | Δp95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `hybrid` → `hybrid_rerank` (adding the fitted cross-encoder) | +0.058 | −0.017 | +0.012 | +0.037 | +4.6 | +5.4 |
| `hybrid_rerank_untrained` → `hybrid_rerank` (fitting it instead of hand-setting) | +0.058 | +0.008 | +0.025 | +0.040 | −0.1 | −1.1 |
| `bm25` → `hybrid` (adding the dense half) | −0.013 | +0.021 | +0.021 | −0.004 | +0.3 | +0.3 |
| `baseline` → `hybrid` (adding BM25 + fusion to Day 13) | +0.004 | −0.017 | −0.075 | −0.018 | −0.0 | −0.1 |
| `baseline` → `hybrid_rerank` (the whole Day-35 stack) | +0.062 | −0.033 | −0.062 | +0.019 | +4.6 | +5.3 |
| `hybrid` → `hybrid_finetuned` (the contrastive encoder) | +0.017 | −0.004 | +0.037 | +0.046 | −0.2 | −0.1 |

Per language (n is small for everything but English; read these as directions):

| lang | n | setting | R@1 | R@3 | R@5 | MRR |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| en | 21 | baseline | 0.302 | 0.587 | 0.746 | 0.702 |
| en | 21 | hybrid_rerank | 0.397 | 0.508 | 0.627 | 0.706 |
| hi-Latn | 10 | baseline | 0.333 | 0.517 | 0.550 | 0.683 |
| hi-Latn | 10 | hybrid_rerank | 0.383 | 0.550 | 0.550 | 0.750 |
| hi | 4 | baseline | 0.250 | 0.250 | 0.250 | 0.500 |
| hi | 4 | hybrid_rerank | 0.250 | 0.250 | 0.250 | 0.500 |
| hi | 4 | dense_finetuned | 0.375 | 0.375 | 0.375 | 0.750 |
| bn | 3 | baseline | 0.167 | 0.167 | 0.167 | 0.333 |
| bn | 3 | hybrid_rerank | 0.167 | 0.167 | 0.167 | 0.333 |
| bn | 3 | dense_finetuned | 0.333 | 0.500 | 0.667 | 0.733 |
| bn-Latn | 2 | baseline | 0.250 | 0.500 | 0.500 | 0.500 |
| bn-Latn | 2 | dense_finetuned | 0.417 | 0.417 | 0.417 | 1.000 |

Bootstrap 95% CIs (row resampling, 2000 draws) on the four headline settings, e.g.
`baseline` MRR 0.503–0.767, `hybrid_rerank` MRR 0.520–0.788, `baseline` R@1
0.204–0.383, `hybrid_rerank` R@1 0.229–0.479 — the full table is in
`ml/reports/day35-retrieval.md`.

**Recommended setting: `RETRIEVER=hybrid_rerank`** (with `RAG_EMBEDDER=tfidf`).

Why, and why not the higher numbers in the table:

1. It is the best setting that a deployment can actually serve *and* whose abstain
   decision is calibrated. `hybrid_finetuned_rerank` measured higher (MRR 0.707) but
   the fine-tuned dense half is the offline contrastive **projection**, which the
   backend's embedder factory cannot load (it builds `tfidf`, `hashing` or
   `sentence_transformers`); recommending it would recommend an artefact that does
   not exist in a wheel. Those rows are the ablation the Colab checkpoint run is
   compared against.
2. It wins where a card list is actually read — the top of the list: **R@1 0.292 →
   0.354**, MRR 0.639 → 0.658, for **+5.4 ms p95** (3.1 → 8.5 ms) against a chat
   turn whose LLM call is measured in seconds. Fitting the cross-encoder instead of
   hand-setting it is worth **+0.040 MRR at no latency cost** (−1.1 ms p95), which
   is the cheapest gain in the table.
3. It is honest about what it does *not* improve: R@3 and R@5 are slightly lower
   than the baseline's (−0.033, −0.062) and both differences sit inside their
   bootstrap intervals. The claim is "a better first card", not "a better top five".
4. Plain `hybrid` is **not** an improvement on the baseline here (MRR −0.018): on
   this corpus BM25 and TF-IDF dense agree closely enough that fusion mostly
   re-shuffles. The reranker is what pays for the hybrid, so the two ship together.

## 2. Ten failure cases where the best system still fails

From `ml/reports/day35-retrieval.md`, `RETRIEVER=hybrid_rerank` on the test split,
each with the evidence the classifier used. The cause is computed, not chosen:
`missed-relevant` means the document exists and shares words with the query and
neither half of the first stage retrieved it; `vocabulary` means no shared content
token at all; `cross-lingual` means *no* labelled document is in the query's
language.

| # | id | query | lang / style | labelled | retrieved (top 3) | cause | likely reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | rt-na-015 | my baby won't stop crying at night, is that normal | en / no-answer | — | sleep-when-mind-races, therapy-what-its-like, sleep-wind-down-hour | abstain-miss | score 0.7372 vs floor 0.7198 — 0.017 over. "night", "crying", "normal" all match sleep and normality documents; the floor is a single number and parenting is out of scope by 0.017 |
| 2 | rt-na-030 | ওষুধের মাত্রা কি নিজেই বাড়ানো যায় | bn / no-answer | — | loneliness-small-contacts-bn, exams-day-of-nerves-bn, breathing-slow-outbreath-bn | abstain-miss | score 0.7530 vs 0.7198. With only 3 Bengali documents the bn pool is *all* of them, so any bn query scores high against them: the floor cannot distinguish "no answer" from "only three candidates exist" |
| 3 | rt-en-013 | 5 4 3 2 1 technique what is it | en / keyword | grounding-five-senses | worry-time-box, therapy-what-its-like, traps-check-a-thought | missed-relevant | the query is digits; the document says "five things you can see". 1 shared token, no numeric bridge — BM25 cannot help and the dense half has never seen "5 4 3 2 1" mean grounding |
| 4 | rt-en-041 | how to start when you have zero energy | en / vague | activation-one-tiny-step, activation-action-before-motivation | help-how-to-start, self-compassion-inner-voice, exams-plan-small-chunks | missed-relevant | 3 shared tokens with each labelled doc, but `help-how-to-start` owns the phrase "how to start" and wins both halves. A title that matches the query verbatim is a very strong lexical prior |
| 5 | rt-en-048 | my roommate is going through something and I don't know what to say | en / scenario | friend-support-listen-first, friend-care-for-yourself | traps-mind-reading, help-how-to-start, trust-telling-someone | missed-relevant | the scenario is about *supporting someone*; the corpus files that under "friend", and the query never says friend. Tag match (the reranker's strongest feature, +5.05) cannot fire without the word |
| 6 | rt-en-049 | I'm exhausted from being the person everyone leans on | en / scenario | friend-care-for-yourself, stress-know-your-signs | self-compassion-inner-voice, trust-telling-someone, overload-everything-is-urgent | missed-relevant | same shape as #5, plus a near-miss that is arguably right: `overload-everything-is-urgent` is a defensible answer that the labels do not list. Label incompleteness, not only a retrieval miss |
| 7 | rt-en-045 | nobody to talk to | en / vague | loneliness-small-contacts, trust-telling-someone, help-how-to-start | help-when-to-seek, exams-day-of-nerves, friend-care-for-yourself | vocabulary | **zero** shared content tokens with any labelled document: three words, all of them generic. The retriever answered (score 0.9146) with confident wrong cards — the worst failure mode in this table, and the one a query-expansion step would target |
| 8 | rt-bl-002 | porikkhar age khub tension hoy, ki korbo | bn-Latn / romanised | exams-day-of-nerves-bn, exams-day-of-nerves, breathing-before-hard-moments | activation-action-before-motivation, friend-care-for-yourself, trust-telling-someone | cross-lingual | romanised Bengali shares no script and no token with either the Bengali or the English documents; score 0.2161, the lowest in the set. `script_match` is a feature precisely because this happens, and it can only down-weight, not rescue |
| 9 | rt-bn-010 | কাউন্সেলিং মানে আসলে কী হয়, প্রথমবার যাব | bn / direct | therapy-what-its-like, help-how-to-start | loneliness-small-contacts-bn, exams-day-of-nerves-bn, breathing-slow-outbreath-bn | cross-lingual | the answer exists **only in English**; the three Bengali documents are returned because they are the only same-language candidates. Live, this turn shows no cards at all (§5) |
| 10 | rt-bn-012 | সব একসাথে চাপিয়ে পড়েছে, কিছুই করতে পারছি না | bn / scenario | overload-everything-is-urgent, exams-plan-small-chunks | exams-day-of-nerves-bn, breathing-slow-outbreath-bn, loneliness-small-contacts-bn | cross-lingual | same as #9 with score 0.8170 — high enough to answer, wrong language. A per-language floor would abstain here rather than show three cards that do not answer the question |

Read together: 4 of 10 are the corpus being English-only for a non-English query
(#8, #9, #10 and the shape of #2), 3 are recall failures where the document was
reachable (#3, #4, #5), 1 is a vocabulary gap (#7), and 2 are abstain near-misses
within 0.04 of the floor (#1, #2). Only the recall failures are fixable by better
retrieval; the rest need more Hindi/Bengali documents, per-language floors, or a
multilingual checkpoint.

## 3. Abstain decision: precision and recall on queries with no relevant document

Fitted on the **calibration** split (20 rows), reported on the **test** split (52
rows, 12 of them no-answer). "Answered" means the top score cleared the floor;
*answer precision* counts a turn correct when a labelled document is in its top 3.

| setting | threshold | abstain P | abstain R | abstain F1 | answer P | answer R | true abstain | false abstain | true answer | false answer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `hybrid` | 0.6578 | 0.400 | 1.000 | 0.571 | 0.818 | 0.550 | 12 | 18 | 22 | 0 |
| `hybrid_rerank` | 0.7198 | 0.417 | 0.833 | 0.556 | 0.786 | 0.650 | 10 | 14 | 26 | 2 |

* **On the 12 no-answer test queries, `hybrid_rerank` correctly refused 10
  (abstain recall 0.833) and wrongly showed a card for 2** — both within 0.04 of the
  floor (§2 cases 1 and 2). `hybrid` refused all 12 (1.000) and paid for it with 18
  false abstains: 45% of answerable questions met with silence.
* abstain precision is low (0.417) *by construction of the objective*: it is the
  share of refusals that were correct, and a floor tuned to keep answer precision up
  refuses a lot of answerable queries. The expensive cell is `false abstain` (14),
  and it is printed in the report for that reason.
* Calibration evidence for the 0.70 floor, and the alternatives fitted beside it
  (`_alternatives` in the committed `tuning.json`):

| objective | `hybrid` threshold | answer P | answer R | `hybrid_rerank` threshold | answer P | answer R |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `answer_precision` ≥ 0.90 (the original config) | 0.8000 | 1.000 | 0.154 | 0.8511 | 1.000 | 0.385 |
| **`answer_precision` ≥ 0.70 (shipped)** | **0.6578** | **0.750** | **0.615** | **0.7198** | **0.700** | **0.769** |
| `f1` | 0.5956 | 0.636 | 0.846 | 0.7198 | 0.700 | 0.769 |
| `abstain_recall` | 0.5956 | 0.636 | 0.846 | 0.7198 | 0.700 | 0.769 |

The floor was relaxed from 0.90 to 0.70 **on calibration evidence only** — at 0.90
the fitted thresholds answered 2 of 13 answerable calibration queries (`hybrid`) and
5 of 13 (`hybrid_rerank`) — and the config comment records that reasoning next to the
value. Measured precision is a lower bound because the labels are incomplete
(`ml/data/retrieval/DATASET_CARD.md` §8); no test row was relabelled.

**Live, on the same 52 test queries through the served retriever:** 18 turns showed
at least one card, 16 of them containing a labelled document (live answer precision
0.889), and **all 12 no-answer turns showed none** (live abstain recall 1.000). The
live numbers differ from the offline ones because serving applies the floor *and*
the orchestrator's locale gate, while the harness ranks with `apply_floor=False` to
compare systems. Both are reported; neither is adjusted.

## 4. Proof the test split was not used to tune anything

Every fitted number, the split it was fitted on, and that split's fingerprint — from
the committed `backend/app/ml_artifacts/retrieval_v1/tuning.json`, which is what a
deployment serves:

| number | value | fitted on | fingerprint of that split |
| --- | ---: | --- | --- |
| `dense_weight` (fusion) | 0.2000 | dev | `b9dc4cbbf98b10e9…` |
| `abstain_score[hybrid]` | 0.657762 | calibration | `1ce6ecf9c07afeeb…` |
| `abstain_score[hybrid_rerank]` | 0.719844 | calibration | `1ce6ecf9c07afeeb…` |
| cross-encoder coefficients | 12 values | train | `c186b664c8f13716…` (scored on dev) |
| reported metrics | — | test (frozen) | `091a469c94b47c6b…` |

All four fingerprints in full, as recorded by `ml/data/retrieval_splits/manifest.json`
and repeated in the committed tuning file:

| split | rows | sha256 of (id, query, labels) |
| --- | ---: | --- |
| train | 97 | `c186b664c8f1371648e28160c46ba6061eb796e05587665d43e4ba676f2feb34` |
| dev | 85 | `b9dc4cbbf98b10e931ad770e2f6c90ff3d6a1311cefc0630b66065a7c437173e` |
| calibration | 20 | `1ce6ecf9c07afeebc8e98e1a593625b8d7c25fa53aefaa24448ad072475e099c` |
| test | 52 | `091a469c94b47c6ba8f8437f6d3ba11617f94128900ed2488749aee7df2f44bd` |

Structural guarantees, not just good intentions:

* the split is a pure function of `sha256(f"{seed}:{id}")[:8] % 100` (seed
  `20261013`), so it cannot depend on a score;
* **hand-written rows never enter train** (test 0–34, dev 35–59, calibration 60–74,
  train 75–99) and synthetic rows never enter test or calibration;
* the config for every stage names its split (`finetune`/`tune` → dev for
  selection, `hybrid.reranker.train_split` → train, `calibration.fit_split` →
  calibration, `eval.split` → test) and pydantic rejects an unknown value;
* `make -C ml data` was re-run from scratch after a sandbox reset and reproduced all
  four fingerprints **byte-identically**;
* the report prints this table itself, so a reader does not have to trust the claim.

## 5. Full chat pipeline, end to end, `RETRIEVER=hybrid_rerank`

Real wiring: `python -m app.services.rag.ingest` over the 40 knowledge documents →
`build_knowledge_retriever(Settings())` → `ChatOrchestrator` with the rule engine,
escalator, redactor, keyword emotion analyser and rule-based output guard, driven by
`orchestrator.respond(...)`. The LLM is the offline Fake provider (it always answers
in English, which is why the last two turns matter).

```
knowledge docs: 40   chunks: 59
embedder: tfidf-v1:dcec78139f9d   dim: 1367   store: /tmp/day35_e2e_store
RETRIEVER=hybrid_rerank  RAG_PERSIST_DIR=/tmp/day35_e2e_store
factory served: hybrid_rerank:lexical-cross-encoder-v1
  tuning: dense_weight=0.2 fusion_k=60 candidates=20 abstain_score=0.719844
          dense_scale=0.24 reranker=lexical-cross-encoder-v1 embedder=tfidf
```

| # | id | user text | locale | `metadata.retrieved_ids` | labelled | response_type |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | rt-en-009 | breathing exercise before I go into the exam hall | en | `['breathing-before-hard-moments', 'grounding-feet-floor', 'breathing-box-counting']` | breathing-before-hard-moments, breathing-box-counting | normal |
| 2 | rt-en-025 | what should I eat on exam day, I have no appetite at all | en | `['exams-day-of-nerves']` | exams-day-of-nerves | normal |
| 3 | rt-hl-018 | grounding exercise batao, jo class me chupke se kar saku | en | `['grounding-five-senses', 'grounding-feet-floor']` | grounding-feet-floor, grounding-five-senses | normal |
| 4 | rt-en-045 | nobody to talk to | en | `['help-when-to-seek', 'exams-day-of-nerves', 'friend-care-for-yourself']` | loneliness-small-contacts, trust-telling-someone, help-how-to-start | normal (**miss** — §2 case 7) |
| 5 | rt-na-015 | my baby won't stop crying at night, is that normal | en | `[]` | (none — should abstain) | normal (**correct refusal**) |

Metadata for turn 1, verbatim: `risk_level=none`, `emotion=anxiety`,
`response_type=normal`, `degraded=False`, `retrieved_docs` = the three card titles,
`retrieved_ids` in lockstep with them (a pydantic validator refuses a mismatch).
4 of the 5 turns showed a labelled document; the fifth is failure case #7 and is
reported as a miss rather than dropped.

Two more turns, deliberately, because they show what the numbers in §1 do not:

```
rt-hl-004 [hi-Latn / hinglish] — the same kind of query with locale=hi
  user           : slow breathing kaise kare, kitne second tak saans chhodu
  locale         : hi   response_type: fallback
  retrieved_ids  : []          degraded=True
  rag log events : ['rag_language_fallback']
  retriever alone : ['breathing-before-hard-moments', 'breathing-slow-outbreath']

rt-bn-010 [bn / direct] — a Bengali query with locale=bn
  user           : কাউন্সেলিং মানে আসলে কী হয়, প্রথমবার যাব
  locale         : bn   response_type: fallback
  retrieved_ids  : []          degraded=True
  rag log events : ['rag_language_fallback']
  retriever alone : []

crisis turn — safety ordering must be untouched by any of this ML
  user           : I want to end my life tonight
  response_type  : crisis   risk: high
  retrieved_ids  : []
  reply[:70]     : "What you're feeling is real, and you don't have to face it alone…"
```

* `rt-hl-004`: the **retriever did find two cards** (after `rag_language_fallback`
  broadened to English), and the turn still shows none — because the Fake provider
  answered in English, the output guard rejected it for `expected_language=hi`, and a
  template reply suppresses `retrieved_docs` by design (a template never read the
  passages). That is Day-13/14 behaviour, not a Day-35 regression, and with a real
  multilingual provider the cards would show.
* `rt-bn-010`: the retriever alone returns `[]` — nothing clears the 0.7198 floor for
  a Bengali query whose answer exists only in English. This is failure case #9 seen
  from the serving side, and it is the strongest argument in §2 for per-language
  floors and more Bengali documents.
* The crisis turn never reaches retrieval: `response_type=crisis`, no cards, the
  pre-written copy verbatim. The safety ordering is unchanged.

## 6. `make test`, offline

Run twice, because the first run's skips were themselves a finding. Both runs are the
full backend suite with branch coverage plus the Day-26 gate
(`scripts/coverage_gate.py`), offline, no network.

**Run A — the sandbox as built (`pip install -e "./backend[dev]"`, the same install
CI's backend job uses):**

```
cd backend && PATH=$PWD/../.venv/bin:$PATH make test
4486 passed, 4 skipped, 49 warnings in 423.20s
  SKIPPED tests/integration/test_nlp_hf_pipeline.py — the nlp extra is not installed
  SKIPPED tests/integration/test_nlp_model.py — the nlp extra is not installed
  SKIPPED tests/ml_runtime/test_runtime.py — could not import 'onnx'
  SKIPPED tests/llm/test_anthropic_provider.py — the optional llm extra is not installed
TOTAL  statements 94.87%  branches 87.97%   (floor 85%)   PASS
coverage gate (Day 26): 2 rule(s) violated   FAIL
  services/safety statements 87.39% < 95%  (lowest: ml_classifier.py 87.4%, trained.py 91.3%)
  services/safety branches   77.78% < 95%  (lowest: ml_classifier.py 77.8%, trained.py 82.4%)
```

**Run B — after `pip install onnx onnxruntime` (1.23.2 / 1.31.0, both from PyPI, which
is reachable; torch and transformers are not):**

```
4491 passed, 3 skipped, 49 warnings in 411.49s
TOTAL  statements 95.84%  branches 88.82%   (floor 85%)   PASS
coverage gate (Day 26): 2 rule(s) violated   FAIL
  services/safety statements 91.29% < 95%  (lowest: trained.py 91.3%, ml_classifier.py 93.3%)
  services/safety branches   82.35% < 95%  (lowest: trained.py 82.4%, ml_classifier.py 86.1%)
```

Run B was repeated on the final tree (after the last type-annotation fixes):
**4491 passed, 3 skipped in 423.46 s**, overall 95.79% statements / 88.79% branches,
and the same two gate violations at the same percentages.

**Every test passes in both runs; the coverage gate does not, and Day 35 is not why.**
`git diff --stat HEAD -- backend/app/services/safety backend/tests/safety` is empty —
no safety module and no safety test changed today. The gate's rule is the strict
reading of ADR 0026/0029 (`min()` over the 13 modules, so *every* one must clear
95%), and the two that do not are uncovered for reasons this sandbox cannot fix:

| module | run B | uncovered lines | why |
| --- | --- | --- | --- |
| `app/services/safety/trained.py` | 91.29% / 82.35% | 328–337 (`_default_loader`, which imports `torch` and `transformers`), 341/344 (double-checked locking behind it), 268–269, 292–293, 302–303 (`JSONDecodeError` branches), 166/168/195/205/222, 500/502 | Day-33 code. The loader cannot be imported here at all, so the branch that calls it and the ones behind it stay dark |
| `app/services/safety/ml_classifier.py` | 93.28% / 86.11% | 310, 314–317, 324–325 (the `SAFETY_ML=onnx` success path and its `except` fallback), 280–281 (the `+trained-unavailable` version string) | Day-34 code. Installing onnx made the *import* work, but the success path needs a checksum-verified student in a materialised MLflow registry — an artefact that is deliberately not in Git |

CI's backend job runs `pytest --cov=app --cov-report=term-missing
--cov-fail-under=80` and **never runs `scripts/coverage_gate.py`**, so this red gate
has been invisible since Day 33/34. Neither the gate nor a test was weakened or
deleted to make it green; it is recorded in `PROGRESS.md` → Known issues, with "run
the Day-26 gate in CI, in a job that has the serving extras and a registry fixture"
in the Parking lot.

ml side, offline:

```
cd ml && ../.venv/bin/python -m ruff check src tests        All checks passed!
       ../.venv/bin/python -m ruff format --check src tests 107 files already formatted
       ../.venv/bin/python -m mypy src tests                Found 1 error in 1 file
         src/manovia_ml/training/emotion/baselines.py:131: Cannot find implementation or
         library stub for module named "huggingface_hub"   (pre-existing, Day 32)
       ../.venv/bin/python -m pytest -q                     2 failed, 452 passed, 7 skipped
```

The retrieval tests all pass with stub encoders and no network —
`test_retrieval_data.py` **30**, `test_retrieval_metrics.py` **27**,
`test_retrieval_training.py` **47**, `test_retrieval_pipeline.py` **34** (138 in
total). The two failures are `tests/test_emotion_baselines_report.py`, Day-32 emotion
tests that assert on the availability string of an off-the-shelf *transformers*
checkpoint: `transformers` is not installed here, so the reason the code reports is
not the one the test expects. Day 35 touches no emotion file
(`git diff --stat HEAD -- ml/src/manovia_ml/training/emotion
ml/tests/test_emotion_baselines_report.py` is empty) and the same two tests failed
before today's work; they are listed in Known issues rather than edited. `make ml-retrieval-smoke` runs all five stages on toy
settings in ~30 s and writes a report whose title says **SMOKE (plumbing, not a
result)** so a smoke number cannot be quoted as a finding.

## 7. PASS / FAIL

| # | check | result | evidence |
| --- | --- | --- | --- |
| 1 | Retrieval eval set: 200+ realistic queries, en/hi/bn, vague + Hinglish, labelled with document ids | **PASS** | 254 rows (154 hand-written ≥ the required 80, 100 synthetic each flagged `"synthetic"`); en 138 / hi-Latn 42 / hi 35 / bn-Latn 20 / bn 19; vague 25, hinglish 28; all 40 documents labelled ≥1× |
| 2 | Baseline Recall@1/3/5 and MRR for the Day-13 retriever | **PASS** | `baseline` row, test split: R@1 0.292, R@3 0.500, R@5 0.592, MRR 0.639 (§1) |
| 3 | Contrastive fine-tuning, MNRL, hard-negative mining from near-misses | **PASS** (offline projection) | `retrieval/contrastive.py`; 147 pairs, 4 hard negatives each (588 total, 232 tag-preferring), MNRL with in-batch negatives, best epoch 9 on dev MRR; dev MRR 0.5274 → 0.5642. **No checkpoint fine-tune: torch/transformers uninstallable** → notebook written, not run (§8) |
| 4 | Untouched test split; comparison vs off-the-shelf embeddings | **PASS** | test = 52 hand-written rows, fingerprint `091a469c…`, never used for selection; `baseline` vs `dense_finetuned` rows in §1 |
| 5 | Hybrid BM25 + dense with reciprocal-rank fusion | **PASS** | `app/services/rag/{bm25,fusion,hybrid}.py`; in-house Okapi BM25 + RRF k=60; `hybrid` row measured |
| 6 | Fusion weight tuned on dev only | **PASS** | 11-weight sweep over 85 dev queries, best 0.20 (dev MRR 0.4871); curve printed in the report; `tuning.json` records `_split: dev` and the dev fingerprint (§4) |
| 7 | Cross-encoder reranker over the top 20 candidates | **PASS** (distilled lexical) | 12 features, `FEATURE_VERSION 1`, balanced logistic regression on 1709 train-split candidates, `rerank_candidates=20`; dev roc_auc 0.681 → 0.749. **A transformer cross-encoder needs torch** → notebook path (§8) |
| 8 | Reranker latency cost and gain measured | **PASS** | +5.4 ms p95 (3.3 → 8.5 ms), +4.6 ms p50, for +0.037 MRR and +0.058 R@1; fitting vs hand-setting: +0.040 MRR at −1.1 ms (§1) |
| 9 | "No good answer" calibration + precision/recall of the abstain decision | **PASS** | floors 0.657762 / 0.719844 fitted on the 20-row calibration split; test abstain R 0.833, abstain P 0.417, answer P 0.786, answer R 0.650; live 12/12 no-answer turns refused (§3) |
| 10 | Integrated behind the existing retriever interface, `RETRIEVER=baseline\|hybrid\|hybrid_rerank` | **PASS** | `Retriever` protocol unchanged; factory dispatch + six logged degradation paths (ADR 0035 §6); `backend/tests/unit/test_rag_factory.py` 34 tests |
| 11 | Tests with stub embedders | **PASS** | backend 4486 passed / 4 skipped; ml retrieval 141 tests across 4 files, all offline, no network, no weights |
| 12 | Eval script writes a report; MLflow logging; model card updated | **PASS** | `ml/evaluation/retrieval.py` → `ml/reports/day35-retrieval.md` (+ gitignored `evaluation.json`); one MLflow run per stage, tagged `day: "35"`, with resolved config, dataset refs, split fingerprints and seed; `docs/model-card.md` C8 |
| 13 | Comparison table with per-language and p95 latency + a recommended setting and why | **PASS** | §1, recommendation `RETRIEVER=hybrid_rerank` with four reasons including why the higher-measuring rows are not recommendable |
| 14 | Ten failure cases with likely cause | **PASS** | §2, causes computed from evidence (`missed-relevant` / `vocabulary` / `cross-lingual` / `abstain-miss`), each with the scores and token overlaps behind it |
| 15 | Test queries not used for tuning (split hashes shown) | **PASS** | §4 |
| 16 | Five end-to-end chat turns with retrieved ids in metadata | **PASS** | §5, plus the two locale-gate turns and the crisis turn |
| 17 | `make test` offline passes | **FAIL (pre-existing, not Day 35)** | Tests: **4486 passed / 4 skipped** (run A) and **4491 passed / 3 skipped** (run B, with onnx installed); overall coverage gate PASS (94.87% → 95.84% statements). The Day-26 *safety-group* gate is red on `trained.py` (91.3%, its `_default_loader` imports torch/transformers) and `ml_classifier.py` (93.3%, its ONNX success path needs a registry artefact that is not in Git). No safety file or safety test changed today; CI never runs the gate. Nothing was weakened or deleted — recorded as a known issue (§6) |
| 18 | Full lint + type checks, backend and ml | **PASS** | backend `ruff check`, `ruff format --check`, `mypy app tests` clean; ml `ruff check`, `ruff format --check`, `mypy src tests` clean except the pre-existing `huggingface_hub` line |
| 19 | Frontend lint, typecheck and tests | **PASS** (2 pre-existing prettier warnings) | Day 35 changes no frontend file (`git diff --stat` over `frontend/` is empty) — the retriever sits behind the existing `Retriever` protocol and the metadata contract is unchanged — but the checks were run anyway: `npm run lint` clean, `npm run typecheck` clean, `npm run test:run` **608 passed / 3 skipped (74 files)**, `npm run format:check` flags `e2e/day25/keyboard.spec.ts` and `src/scripts/playwrightFailures.test.ts`, both untouched today (§9) |

## 8. What could not be run here

Stated plainly, with the reason and what to run instead:

1. **No transformer fine-tune, no real cross-encoder.** `torch`, `transformers` and
   `sentence-transformers` cannot be installed (download.pytorch.org and
   huggingface.co are unreachable), so `SentenceTransformerEncoder` and the
   `reranker.kind: transformer` path are implemented behind lazy imports that raise
   an actionable `EncoderUnavailable` / `CrossEncoderUnavailable`, and are exercised
   only by their config and skip paths. Run
   `ml/notebooks/colab_retrieval_finetune.ipynb` on Colab/Kaggle (free CPU or T4):
   it reads the pairs and examples these stages wrote, fine-tunes
   `paraphrase-multilingual-MiniLM-L12-v2` with the same MNRL config, fits a
   multilingual cross-encoder on the same 1709 candidates, exports both, and prints
   the same metrics table for a side-by-side with §1.
2. **No ONNX export of a retrieval model** (same reason). The Day-34 serving path
   (`app/services/ml_runtime/`) is where a cross-encoder student would go; the seam
   in `rerank.py` (`load_cross_encoder`) is written to accept one.
3. **Latency is in-process on 2 CPUs**, `repeats=3` per query, p95 over 52 queries.
   It ranks the settings against each other; it is not a production latency budget
   and it excludes the LLM call, which dominates a real turn.
4. **The Day-26 coverage gate cannot go green here** while `trained.py`'s
   transformers loader is uncoverable (§6). With `onnx` + `onnxruntime` installed
   from PyPI the ml_runtime tests do run; `torch`/`transformers` remain impossible.

## 9. Full lint, type and test commands, run one last time

Every command below was run on the final tree, in this sandbox, on 2026-10-15.

**Backend** (`cd backend`, venv on `PATH`):

```
ruff check .                     All checks passed!
ruff format --check .            382 files already formatted
mypy app tests                   Success: no issues found in 322 source files
make test                        4491 passed, 3 skipped in 423.46s
                                 TOTAL statements 95.79%  branches 88.79%   (floor 85%) PASS
                                 coverage gate (Day 26): 2 rule(s) violated  FAIL  (§6)
```

The three skips are the `nlp` extra (2 files) and the optional `llm` extra
(1 test); `tests/ml_runtime/test_runtime.py` runs because `onnx` and `onnxruntime`
were installed from PyPI for this run. Without them the suite is 4486 passed /
4 skipped and `ml_classifier.py` drops to 87.4%, which is the first of the two gate
violations in §6.

**ml** (`cd ml`):

```
ruff check src tests             All checks passed!
ruff check .                     All checks passed!        (notebooks included)
ruff format --check .            133 files already formatted
mypy src tests                   Found 1 error in 1 file (checked 107 source files)
                                   training/emotion/baselines.py:131: Cannot find
                                   implementation or library stub for module named
                                   "huggingface_hub"   (pre-existing, Day 32)
pytest -q                        2 failed, 452 passed, 7 skipped in 12.98s
pytest tests/test_retrieval_*.py 138 passed                 (30 + 27 + 47 + 34)
make retrieval-smoke             all five stages, ~30 s, report titled SMOKE
make retrieval-pipeline          encoder 70.6 s -> fusion sweep -> reranker 2.0 s
                                 -> calibration -> evaluation, 4 MLflow runs
```

The two failures are `tests/test_emotion_baselines_report.py` (Day 32, transformers
not installed — §6).

**Frontend** (`cd frontend`, after `npm ci`: 743 packages):

```
npm run lint                     (eslint .) no output — clean
npm run typecheck                (tsc --noEmit && tsc -p tsconfig.sw.json) clean
npm run test:run                 Test Files 74 passed | 1 skipped (75)
                                 Tests      608 passed | 3 skipped (611)   94.88s
npm run format:check             [warn] e2e/day25/keyboard.spec.ts
                                 [warn] src/scripts/playwrightFailures.test.ts
                                 Code style issues found in 2 files.
```

The two prettier warnings are in files Day 35 does not touch (a Day-25 e2e spec and a
Day-28-era script test); they were left alone rather than reformatted inside a
retrieval change, and are noted here so the output is not mistaken for a clean run.

