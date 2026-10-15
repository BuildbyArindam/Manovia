# ADR 0035: Fine-tuned retrieval — contrastive embeddings, hybrid BM25 + dense fusion, a distilled cross-encoder and a calibrated abstain floor

- Status: accepted (Day 35)
- Date: 2026-10-15
- Deciders: Manovia maintainers
- Supersedes: none. Extends [0013 knowledge retrieval (RAG)](0013-knowledge-retrieval-rag.md), which shipped one dense retriever over a TF-IDF embedder and a single similarity floor.
- Related: [0023 multilingual](0023-multilingual-english-hindi-bengali.md), [0024 evaluation harness](0024-evaluation-harness.md), [0028 model card gate](0028-model-card-and-clinical-review-gate.md), [0031 ML platform](0031-ml-platform.md), [0034 compression and serving](0034-compression-serving-rollout.md), [`ml/configs/retrieval_*.yaml`](../../ml/configs/), [`docs/verification/day35-retrieval.md`](../verification/day35-retrieval.md)

## Context

Day 13's retriever answers a chat turn with up to three knowledge cards. It is one
system: cosine similarity over TF-IDF vectors of 59 passages from 40 documents,
with a single hand-set floor (`RAG_MIN_SIMILARITY`) that decides whether a turn
gets cards at all. Three things were visibly wrong with it, and none of them could
be argued about properly because there was no measurement:

1. **Nobody knew how good it was.** There was no retrieval eval set, so "the cards
   feel irrelevant" was unfalsifiable, and every change to the floor was a guess.
2. **A lexical query with no shared vocabulary got nothing.** "how do I stop a
   panic attack" returns `[]` from the Day-13 retriever: the corpus answers it
   (`breathing-slow-outbreath`, `grounding-five-senses`) but the dense cosine
   (0.102) never clears the floor (0.12).
3. **The floor was one number doing two jobs** — "is this passage similar enough to
   be worth ranking" and "should this person see a card at all" — and it was set by
   hand, so it was wrong for one of them and unverifiable for the other.

The constraints are the standing ones: no GPU and no hub access in the build
sandbox (torch, transformers and sentence-transformers cannot be installed here),
no weights or datasets in Git, config as data, every number from a frozen split,
`ml/` fully offline, and the safety pipeline's ordering untouched by anything ML
(AGENTS.md ground rules 1–10, ADR 0008/0009).

## Decision

### 1. An eval set first, with splits frozen before anything is fitted

`ml/data/retrieval/retrieval_queries.jsonl`: **154 hand-written queries**, each
labelled with the document ids that answer it, plus **59 no-answer rows** (42
out of scope — crypto, parenting, medical dosing — and 17 the corpus genuinely
lacks). Languages: en 138, hi-Latn 42, hi 35, bn-Latn 20, bn 19; styles: no-answer
55, direct 53, keyword 50, scenario 32, hinglish 28, vague 25, romanised 11. All
40 documents are labelled by at least one query. No self-harm or crisis query is in
the set: retrieval is step 5 of the pipeline and a HIGH turn never reaches it, so a
crisis query in a retrieval eval would measure a path that does not exist.

`ml/data/retrieval/augment.py` adds **100 template-paraphrase rows** (every one
flagged `"synthetic": true` with its template id) to reach 254. Synthetic rows are
never in the test split.

`retrieval_split` (DVC) cuts four ways by hashed bucket, so the cut is a function of
the seed and the row id and nothing else: **train 97, dev 85, calibration 20,
test 52** — and test is hand-written rows only. The fingerprints
(`sha256` over id + query + labels) are recorded in the split manifest *and* in the
committed `tuning.json`, so "the test split was not used to tune" is checkable
rather than asserted:

| split | rows | sha256 |
| --- | ---: | --- |
| train | 97 | `c186b664c8f1371648e28160c46ba6061eb796e05587665d43e4ba676f2feb34` |
| dev | 85 | `b9dc4cbbf98b10e931ad770e2f6c90ff3d6a1311cefc0630b66065a7c437173e` |
| calibration | 20 | `1ce6ecf9c07afeebc8e98e1a593625b8d7c25fa53aefaa24448ad072475e099c` |
| test | 52 | `091a469c94b47c6ba8f8437f6d3ba11617f94128900ed2488749aee7df2f44bd` |

The fusion weight is fitted on **dev**; the abstain floors on **calibration**; the
reported numbers come from **test**, once, at the end.

### 2. Contrastive fine-tuning with MNRL and mined hard negatives — offline projection here, checkpoint on Colab

`retrieval/pairs.py` builds (query, positive passage) pairs from the train split
(147 pairs from 80 queries, up to 2 positives each) and mines **4 hard negatives
per pair** out of the top-20 near-miss passages, preferring passages that share the
positive's tag (232 of 588 negatives were tag-preferring; no pair came away empty).
A near-miss that is topically adjacent is the negative that teaches an embedding
something; a random document teaches nothing.

`retrieval/contrastive.py` implements **MultipleNegativesRankingLoss** exactly as
sentence-transformers defines it — in-batch negatives, labels on the diagonal,
temperature-scaled softmax cross-entropy — over a *frozen* lexical feature matrix
(word 1–2 + char 3–5 TF-IDF, 8000 + 8000, sublinear) with a trained 128-dim
projection: scale 20, 12 epochs, batch 16, lr 0.05, best epoch chosen on dev MRR
(epoch 9). 70.6 s on 2 CPUs, MLflow run `b4e61549403a441e8e8fd80186849f99`.

The same config, the same pairs and the same loss train
`paraphrase-multilingual-MiniLM-L12-v2` in
`ml/notebooks/colab_retrieval_finetune.ipynb`; the notebook is the real fine-tune
and the projection is the sandbox stand-in that proves the plumbing and provides the
ablation. **The projection is not servable** — the backend's embedder factory builds
`tfidf`, `hashing` or `sentence_transformers`, not an ml matrix — so the
`*_finetuned` rows in the report are measurements, not deployment settings, and the
recommendation is never chosen from them.

### 3. Hybrid retrieval: in-house BM25 + dense, reciprocal-rank fusion, weight fitted on dev only

`app/services/rag/bm25.py` is Okapi BM25 (k1 1.2, b 0.75, title terms indexed
`title_boost` = 2.0 extra times, IDF floor 0.5) written in-house because `rank-bm25`
is not a dependency and adding one to compute 59 documents' scores was not the
trade. `app/services/rag/fusion.py` is reciprocal-rank fusion with k = 60 over the
two ranked lists, weighted by `dense_weight`.

The sweep is over dev only, 11 weights: dev MRR 0.4366 at `dense_weight=0.0`
(BM25 alone), **0.4871 at 0.20**, 0.4809 at 1.0. The curve is printed in the report
because a peak one query wide is not a finding. `dense_scale` — the factor that puts
a cosine and a BM25 score on comparable footing — is **derived at build time as
2 × the embedder's own floor** and deliberately *not* persisted: a served embedder's
floor can be changed by configuration, and a stale scale in a JSON file would
silently override it.

### 4. The cross-encoder is distilled, not downloaded

`app/services/rag/rerank.py` scores a (query, candidate) pair with **12 features**
(bias, fused confidence, dense cosine, saturated BM25 with pivot 5.0, token / IDF /
title / bigram / char-trigram overlap, script match, tag match, log query length)
under `FEATURE_VERSION = 1`. `ml/.../retrieval/reranker.py` fits a **balanced
logistic regression** (c = 1.0) over the candidates the first stage actually
produced for the train split — 1709 labelled candidates, 96 relevant — and scores
the fit on dev: roc_auc 0.6814 → **0.7488**, MRR 0.6138 → **0.6430**,
relevant_first 0.4906 → 0.4906 (unchanged: the fit reorders within the top of the
list, it does not rescue documents the first stage never surfaced). 2.0 s, MLflow
run `bded05be3f28444c8f3eeac27ebb1728`.

The real cross-encoder (a multilingual MiniLM cross-encoder, or a distillation of
one) is the notebook path; the lexical scorer is what serves here, and the report
names which one produced every number. The loader refuses a feature-list, version or
BM25-pivot mismatch rather than scoring with coefficients that mean something else.

### 5. "No good answer" is a calibrated decision with a recorded operating point

One score floor per served setting, fitted on the **calibration** split
(`retrieval/tuning.py --stage abstain`): the lowest threshold whose *answer
precision* — of the turns that got a card, how many had a relevant document in the
top 3 — is at or above the configured floor. Fitted: `hybrid` **0.657762**,
`hybrid_rerank` **0.719844**.

The floor started at 0.90 and the calibration split rejected it: at 0.90 the fitted
thresholds answered 2 of 13 answerable calibration queries (`hybrid`) and 5 of 13
(`hybrid_rerank`). A retriever that is right almost every time it speaks and silent
almost always is not a feature anybody experiences as help, so the config floor is
**0.70**, with the reason and the measured cost recorded in the config comment. The
other two objectives (`f1`, `abstain_recall`) are fitted as well and stored beside
the shipped one under `_alternatives`, because a threshold is a trade-off and the
trade-off is the part a reviewer needs.

Precision measured against these labels is a **lower bound**: the eval set's labels
are incomplete (`ml/data/retrieval/DATASET_CARD.md` §8 records the known gaps), so a
correct card for an unlabelled document is counted as a wrong one. Test rows were
never relabelled to make a number look better.

### 6. Behind the existing seam: `RETRIEVER=baseline|hybrid|hybrid_rerank`

The `Retriever` protocol from ADR 0013 is unchanged — the orchestrator, the prompt
builder and the metadata contract do not know which system answered. Degradation is
explicit and logged, never silent:

| situation | behaviour |
| --- | --- |
| no `tuning.json` | documented defaults + `rag_tuning_missing` |
| `tuning.json` fitted for another embedder | ignore its numbers + `rag_tuning_embedder_mismatch` |
| unknown key in `dev` | skip it + `rag_tuning_unknown_keys` |
| no floor for the served setting | default floor + `rag_tuning_no_abstain_for_setting` |
| `hybrid_rerank` with no loadable coefficients | **downgrade to `hybrid`** + `rag_retriever_downgraded` |
| environment override (`RAG_DENSE_WEIGHT`, …) | env wins + `rag_tuning_env_override` |

The downgrade is a *setting* downgrade, not a threshold downgrade: serving hand-set
coefficients under a floor calibrated on fitted ones would put an uncalibrated number
where a calibrated one is promised.

### 7. What is committed, and what is not

`backend/app/ml_artifacts/retrieval_v1/tuning.json` **is committed**: it is
calibration data — a fusion weight, two abstain floors, the provenance of each and
the four split fingerprints — and a reviewer must be able to read the numbers a
deployment serves. The fitted cross-encoder coefficients are **not** (they are
weights, AGENTS.md ground rule 3): `make ml-retrieval-rerank` regenerates and
installs them, they are gitignored, and a wheel built from Git serves plain
`hybrid` with a logged downgrade. The 154 hand-written queries and their labels are
committed (a card and a catalogue entry, no personal data); the derived expansion,
the splits and every artefact directory are DVC/MLflow outputs and gitignored.

### 8. Safety ordering is untouched

Retrieval is still step 5. A HIGH or IMMINENT turn is answered by the pre-written
crisis copy and never reaches the retriever; a template reply suppresses
`retrieved_docs` by design, because a template never read the passages and showing
their cards would claim a connection that does not exist. Verified end to end in
`docs/verification/day35-retrieval.md` §5.

## Consequences

**Positive**

- Retrieval quality is now a measured quantity with a frozen test split, bootstrap
  CIs and per-language slices, and the Day-13 retriever has a baseline number
  (test MRR 0.639, Recall@1 0.292) that any future change is compared against.
- The recommended setting (`RETRIEVER=hybrid_rerank`) beats the baseline where it
  matters most — the top of the list: **Recall@1 0.292 → 0.354, MRR 0.639 → 0.658**
  — for **+5.4 ms p95** (3.1 → 8.5 ms), and fitting the cross-encoder rather than
  hand-setting it is worth **+0.040 MRR at no latency cost at all**.
- "No good answer" is a decision with a calibrated threshold, a reported
  precision/recall and a recorded alternative, instead of a constant nobody could
  justify. On test, all 12 no-answer queries were correctly refused.
- Every stage is one `make` target, logged to MLflow with the resolved config, the
  dataset refs and the split fingerprints, and reproducible from a fresh clone
  (`make -C ml data` regenerated all four splits with identical fingerprints).

**Negative / known issues**

- **Recall@3 and Recall@5 did not improve** (0.500 → 0.467 and 0.592 → 0.529 on
  test): the reranker sharpens the top of the list and reorders below it. On 52 rows
  both differences are inside their bootstrap intervals, so the honest claim is
  "better first card, not a better top five".
- **Serving shows fewer cards than the offline harness measures.** Live, 18 of 52
  test turns returned a card (16 of them containing a labelled document) and all 12
  no-answer turns returned none; the offline harness reports answer recall 0.650
  because it ranks with `apply_floor=False` and without the orchestrator's locale
  gate. The two numbers measure different things and both are reported.
- **Non-English locales are the weak spot, and it is a corpus problem.** Only 3 of
  40 documents are Hindi and 3 are Bengali, so a `locale=bn` turn frequently has
  nothing above the floor at all: a Bengali query about what counselling is like
  returns `[]` live. Strict language filtering caps hi/bn recall near 17% no matter
  what the embedder does. The contrastive projection is the one setting that clearly
  helps here (bn MRR 0.333 → 0.733, hi 0.500 → 0.750, bn-Latn 0.500 → 1.000 on
  n = 3/4/2 — small enough to be a direction, not a result).
- **No multilingual checkpoint was trained here.** torch/transformers are not
  installable in the sandbox, so the fine-tuned dense row is the offline projection
  and the cross-encoder row is the distilled lexical scorer. The notebook is written
  and untested on real weights; running it is a Day-36 step.
- **The Day-26 coverage gate is red in this sandbox** on two Day-33/34 safety
  modules (`ml_classifier.py` 87.4%, `trained.py` 91.3% against a 95% floor) whose
  uncovered lines are the ONNX and torch/transformers paths. Day 35 touches no
  safety code; CI's backend job runs `pytest --cov-fail-under=80` and never runs the
  gate, so this was invisible until `make test` was run for real. Tracked in
  PROGRESS.md → Known issues and Parking lot.

**Parking lot** (deliberately not built today): per-language abstain thresholds;
more Hindi/Bengali documents before more retrieval engineering; a real
cross-encoder checkpoint and an ONNX export of it behind the same `rerank.py` seam;
query expansion for the vocabulary-gap failures; adding the coverage gate to CI in a
job that has the serving extras.

## Verification

- `docs/verification/day35-retrieval.md` — the comparison table, ten analysed
  failure cases, the abstain precision/recall, the split-fingerprint proof, five
  end-to-end chat turns with their retrieved ids, and the PASS/FAIL table for the
  day's checks.
- `ml/reports/day35-retrieval.md` (+ the gitignored `evaluation.json`) — generated
  by `make ml-retrieval-eval`, 8 settings on the 52-row test split, bootstrap CIs,
  per-language slices, the dev sweep curve and the failure cases with evidence.
- Commands: `make -C ml data` (splits), `make ml-retrieval-finetune`,
  `make ml-retrieval-tune`, `make ml-retrieval-rerank`, `make ml-retrieval-calibrate`,
  `make ml-retrieval-eval`, `make ml-retrieval-smoke` (plumbing only — its report
  says SMOKE in the title so a smoke number cannot be quoted as a result),
  `cd backend && make test`.
- MLflow: experiment `manovia-ml`, runs tagged `day: "35"` — encoder
  `b4e61549403a441e8e8fd80186849f99`, reranker `bded05be3f28444c8f3eeac27ebb1728`,
  plus one run per tuning and evaluation stage.
