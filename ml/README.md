# Manovia ML platform (`ml/`)

Days 31–37. This directory is the foundation for every model Manovia might ever
put near a user: a **data catalogue** with licences and verdicts, **experiment
tracking** where every run records enough to be reproduced, **configs** that are
validated rather than convention, and **cards** that have to exist before a
number is quoted. Day 32 added the first real model: a **LoRA fine-tune of
`xlm-roberta-base` into Manovia's nine emotions** (ADR 0032), packaged as an
artefact the backend can load with `EMOTION_ANALYZER=trained`. Day 36 adds a
synthetic mood-forecast research pipeline; its sequence-model scores are blocked
in this sandbox because PyTorch is not installed, so only transparent baselines
are reported. Day 37 adds a synthetic contextual-bandit policy evaluation behind
a deterministic safety boundary; it remains offline research, not a serving path.

Nothing here is wired into the API's *safety* path, and the crisis gate is
untouched (AGENTS.md, ML ground rule 1): the emotion model is a tone hint, it
cannot lower a risk level, and a missing artefact falls back to the lexicon.
What the directory adds is the ability to answer, months later, *"where did that
number come from, and can we get it back?"*

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e "./backend[dev]"      # backend pins first (shared deps)
.venv/bin/pip install -e "./ml[ml,dev]"        # this package + mlflow/dvc/sklearn
# Day 32 only: the deep-learning stack (~2 GB). CI installs the CPU-only torch
# wheel first so a pull request never downloads CUDA kernels:
#   pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -e "./ml[training]"     # torch/transformers/peft/optuna
```

or, from the repository root, `make ml-install`. The package is `manovia-ml`
(module `manovia_ml`); `[ml]` is the ML stack (numpy, scikit-learn, joblib,
pandas, mlflow, dvc), `[dev]` is pytest/ruff/mypy, and `[training]` (Day 32) is
torch/transformers/peft/optuna/accelerate. The emotion tests skip themselves with
a message when `[training]` is absent, so a checkout that only wants the linear
path, the catalogue checker or the docs still has a green suite.

## Try it in one command

```bash
make ml-smoke      # dvc repro → catalogue check → toy train → baseline check → report
```

It rebuilds the deterministic splits, checks the catalogue's licences and
verdicts, trains the tiny model, verifies the metrics against the committed
baseline, and writes `ml/reports/smoke/<date>-smoke.md`. Whole thing: a few
seconds on one CPU, no network.

## The seven things every run logs

`manovia_ml.tracking.tracked_run` writes them before the caller does anything, so
a run cannot forget:

| | |
| --- | --- |
| **git commit** | commit, branch, dirty flag, number of changed files (`git.commit`, tags) |
| **data hash** | sha256, path, row count, per-label and per-language counts for every file read |
| **config** | the resolved config as JSON + a sha256 fingerprint (defaults applied) and the source YAML as an artefact |
| **seed** | the seed, the derived sub-seeds' parent, `PYTHONHASHSEED` as read, BLAS thread pinning |
| **library versions** | numpy/scikit-learn/scipy/pandas/joblib/pydantic/pyyaml/mlflow/dvc as params, full package list in `environment.json` |
| **metrics** | accuracy, macro/weighted F1, log loss, per-class F1, in-sample accuracy (`train_accuracy`) |
| **artefacts** | `model/model.joblib` (bundle), `evaluation.json`, `metrics.json`, `config.json`, `data.json`, `environment.json` |

## Layout

| path | what lives there |
| --- | --- |
| `data/CATALOGUE.md` | every dataset used, considered, planned or rejected — licence, allowed use, biases, personal data, verdict. Machine-readable, test-enforced. |
| `data/toy/` | the hand-written 84-sentence fixture (the only data CI trains on) |
| `data/planned/hi_bn_emotion/` | the dataset card for the Hindi/Bengali set that must be written and natively reviewed |
| `configs/` | one YAML per experiment + `expected/` baselines |
| `src/manovia_ml/` | the package: config, seeds, hashing, tracking, data, training, evaluation, serving, reporting |
| `tests/` | 212 tests, offline, including the reproducibility contract and the LoRA path on a tiny random-init model |
| `src/manovia_ml/training/emotion/` | Day 32: the label-mapping table, data conversion and overlap checks, the LoRA trainer, threshold tuning, the Optuna sweep, evaluation and the report/card writer |
| `data/validation/emotion_validation.jsonl` | the hand-written 174-row en/hi/bn held-out set (never trained on, never tuned on) + its dataset card |
| `templates/` | dataset and model card templates |
| `notebooks/` | exploration only (Colab notebook for the first real run) |
| `scripts/fetch_goemotions.sh` | the one network step, as a script rather than a paragraph |
| `reports/` | generated, reviewed, committed reports |
| `docker/mlflow/` | optional MLflow service (the file store is the default) |
| `dvc.yaml`, `dvc.lock` | the data pipeline and the hashes it recorded |

## Commands

| command | what it does |
| --- | --- |
| `make ml-install` | install the package and the ML stack |
| `make ml-data` | `dvc repro`: rebuild the deterministic splits |
| `make ml-status` | does `dvc.lock` match the code, and is the data what the pipeline recorded? |
| `make ml-lock` | only the lock check (`manovia_ml.data.dvc_lock`) |
| `make ml-catalogue` | check the catalogue (licences, verdict rules, generated table) |
| `make ml-train CONFIG=configs/x.yaml` | train, log the run, check the baseline |
| `make ml-record CONFIG=configs/x.yaml` | re-record that config's baseline (a deliberate act) |
| `make ml-eval RUN=<run id>` | re-score a logged run from its own artefacts |
| `make ml-report RUN=<run id>` | write `reports/<run id>.md` |
| `make ml-smoke` | the CI path end to end |
| `make ml-lint`, `make ml-test` | ruff + ruff-format + mypy; pytest |
| `make ml-emotion-smoke` | the Day-32 path, offline: tiny random-init XLM-R, 12 steps, seconds |
| `make ml-emotion-train CONFIG=configs/emotion_lora_xlmr.yaml` | the real run (GPU; see the notebook) |
| `make ml-emotion-eval ARTIFACT=artifacts/emotion/<name>` | score a packaged artefact in a fresh process |
| `make ml-emotion-tune CONFIG=... TRIALS=12` | the Optuna sweep (needs `optuna.enabled: true`) |
| `make ml-emotion-data SRC=... OUT=... SCHEME=goemotions LANG=en` | convert a corpus through the mapping table |
| `ml/scripts/fetch_goemotions.sh` | download + convert GoEmotions (network; needs `datasets`) |
| `make distill-smoke` (in `ml/`) | Day 34 offline path: distil → ONNX → INT8 → gates → report (stand-in teacher, linear student) |
| `make distill-gates METRICS=<pipeline_metrics.json>` | release-gate check; exit 1 rejects the candidate |
| `make registry-status` | MLflow alias + materialised registry state |
| `make retrieval-finetune` | Day 35: mine pairs + hard negatives, fit the contrastive encoder, select the epoch on dev |
| `make retrieval-tune` | sweep the fusion weight on **dev** and install `tuning.json` into the backend |
| `make retrieval-rerank` | fit the 12-feature cross-encoder on **train** candidates and install the coefficients |
| `make retrieval-calibrate` | fit the "no good answer" floors on **calibration** and install `tuning.json` |
| `make retrieval-eval` | measure all eight settings on the configured split and write the report |
| `make retrieval-pipeline` | the five above, in order |
| `make retrieval-smoke` | every stage on toy settings in ~30 s; its report is titled SMOKE (plumbing, not a result) |
| `make mood-forecast` | Day 36: generate synthetic data, compare baselines and (with the optional training extra) GRU/LSTM/Transformer quantiles; logs to MLflow and writes `reports/day36-mood-forecasting.md` |
| `make jitai-sim` | Day 37: run 20-seed synthetic contextual-bandit and logged-data IPS/DR evaluation; logs aggregates to MLflow and writes `reports/day37-jitai.md` |
| `make jitai-smoke` | Day 37: same safety checks and estimators on tiny synthetic episodes (20 small seeds) |

## Day 34: compression, serving and safe rollout

`src/manovia_ml/compression/` distils a Day-32/33 teacher into a small
student (temperature loss, `configs/distill_*.yaml`), exports ONNX,
applies INT8 dynamic quantisation, scores fp32 vs INT8 on the frozen
eval, and checks `configs/release_gates.yaml` — a failed gate rejects
the candidate and blocks promotion. The registry
(`compression/registry.py`) maps staging/production/archived onto
MLflow aliases, verifies SHA-256 at registration and at load, and
materialises the serving directory the backend's ONNX runtime reads
(`SAFETY_ML=onnx`). Serving (batching, warm-up, timeouts, previous-version
fallback) and shadow mode live in `backend/app/services/ml_runtime/`;
ADR 0034 records the decisions, `docs/runbook.md` the rollback.

Sandbox honesty: the committed smoke path uses a TF-IDF stand-in teacher
and a hashed-feature linear student (the Day-32/33 weights and a GPU do
not exist here), with train==eval toy fixtures — its numbers are plumbing.
The real MiniLM students run from `notebooks/colab_distill_export.ipynb`
with `configs/distill_*_student.yaml`: upload the Day-32/33 artefacts,
fill the null `sha256`/`rows` pins from your corpus build, distil, export,
gate, register, promote with `--gates-passed` only on a PASS, then shadow
before serving. Exact owner steps are in PROGRESS.md (Day 34).

## Day 35: fine-tuned retrieval

`src/manovia_ml/retrieval/` is the retrieval half of the ML platform, and
`src/manovia_ml/evaluation/retrieval.py` is the report. The order matters: an eval
set before a model, a frozen split before a knob, a calibration split before a
threshold.

* **`corpus.py`** reads the backend's 40 knowledge documents into 59 passages and
  fingerprints the corpus, so a report says which passages it scored.
* **`data.py`** owns the eval set (154 hand-written queries + 59 no-answer rows) and
  the four-way split — train 97, dev 85, calibration 20, test 52 — cut by hashed
  bucket so the cut cannot depend on a score. Hand-written rows never enter train;
  synthetic rows never enter test or calibration.
* **`augment.py`** paraphrases the hand-written rows into 100 `synthetic` rows
  (template id recorded, label drift screened, 12 candidates dropped). Offline, seeded,
  no LLM call.
* **`metrics.py`** is Recall@k / MRR / hit@k with per-language and per-style slices,
  row-bootstrap CIs, and the abstain confusion matrix plus `fit_abstain_threshold`
  under three objectives.
* **`embeddings.py` / `pairs.py` / `contrastive.py`** are the training stack: four
  encoders behind one protocol (hashing and TF-IDF offline, a trained projection, and a
  lazily imported `sentence_transformers` checkpoint), hard-negative mining over the
  top-N near misses preferring same-tag passages, and
  `MultipleNegativesRankingLoss` implemented over a frozen feature matrix — the same
  loss, config and pairs the notebook feeds a real multilingual checkpoint.
* **`harness.py`** measures eight settings on the **backend's own retrievers**
  (`RETRIEVER=baseline|hybrid|hybrid_rerank`, plus the fine-tuned and untrained
  ablations), ingesting the real corpus once per encoder kind. A missing artefact
  produces a row that says why, never a silently absent row.
* **`train_embedding.py` / `reranker.py` / `tuning.py`** are the run stages; each logs
  to MLflow with the resolved config, the dataset refs, the seed and the split
  fingerprints.

Sandbox honesty: torch, transformers and sentence-transformers cannot be installed
here, so **no multilingual checkpoint was trained**. What was trained is a 128-dim
contrastive projection over lexical features (dev MRR 0.5274 → 0.5642, best epoch 9 on
dev), and it is *not servable* — the backend's embedder factory builds `tfidf`,
`hashing` or `sentence_transformers`, not an ml matrix. Those rows are the ablation a
checkpoint run is compared against, which is why the recommendation is always chosen
among settings that have both a calibrated threshold and a servable encoder. The real
fine-tune is `notebooks/colab_retrieval_finetune.ipynb`.

Measured on the frozen 52-query test split (`ml/reports/day35-retrieval.md`):
`baseline` MRR 0.639 / R@1 0.292 / p95 3.1 ms → **`hybrid_rerank` MRR 0.658 / R@1
0.354 / p95 8.5 ms**, with R@3 and R@5 slightly *lower* than the baseline and both
differences inside their bootstrap intervals. Fitting the cross-encoder instead of
hand-setting it is +0.040 MRR at no latency cost. Decisions:
[ADR 0035](../docs/adr/0035-fine-tuned-retrieval.md); method, failure cases and the
end-to-end chat turns:
[docs/verification/day35-retrieval.md](../docs/verification/day35-retrieval.md).

## Day 36: mood forecasting with sequence models and honest uncertainty

`src/manovia_ml/mood_forecast/` is a research-only pipeline over a deterministic,
synthetic 72-user × 210-day panel. It has four transparent baselines, optional GRU,
LSTM and Transformer models with ordered next-three-day 10/50/90 quantiles, user- and
time-disjoint splits, 80% split-conformal interval expansion on calibration users,
feature-ablation hooks, guarded few-shot last-layer adaptation, and an MLflow report.
The frozen report is [`reports/day36-mood-forecasting.md`](reports/day36-mood-forecasting.md);
the run's data hash and split boundaries are in that report.

Run it with `make mood-forecast` (or `make ml-mood-forecast` from the repository root).
The optional `[training]` extra is required for neural fitting. In a CPU-only checkout
without PyTorch, pass `MOOD_FORECAST_ARGS=--allow-baselines-only` explicitly; the CLI
otherwise refuses to mislabel a baseline run as a complete sequence-model experiment.

**Sandbox result: baselines only.** PyTorch is absent, so GRU/LSTM/Transformer scores,
feature ablations and few-shot adaptation were not measured. The best baseline in this
synthetic run was the 7-day moving average (MAE 0.554, MASE 0.915, 78.3% empirical
coverage at nominal 80%); this is not a neural win or a claim about real users. No model
weights are served. The product's separate Outlook endpoint is a default-off,
explicit-opt-in arithmetic summary of a user's numeric history; its displayed range is
an empirical variability description, explicitly **not** a calibrated confidence
interval or a probability. It never reads mood notes or journal text.

Decisions and limitations: [ADR 0036](../docs/adr/0036-mood-forecasting.md); code and
API/UI verification:
[docs/verification/day36-mood-forecasting.md](../docs/verification/day36-mood-forecasting.md).

## Day 37: contextual-bandit intervention policy (offline research only)

`simulation/jitai_sim.py` is the CLI for the synthetic simulator in
`src/manovia_ml/simulation/jitai.py`. It imports and exercises the backend's
actual `app.services.plan.policy` algorithms and safety wrapper. The checked-in
`configs/jitai.yaml` runs 20 seeds over four heterogeneous synthetic groups,
compares an **offline-only** random reference with epsilon-greedy, LinUCB and
Bayesian Thompson sampling, reports cumulative reward/regret and fairness gaps,
and runs IPS/DR on disjoint logged uniform-behavior data against simulator
truth. It prints five model-feature-based `Why this?` examples. The generated
interaction log is hashed in memory; event rows, personal data and model weights
are not written.

```bash
make jitai-smoke  # tiny episodes, still 20 seeds; local MLflow + /tmp report
make jitai-sim     # full 20-seed evaluation; MLflow + reports/day37-jitai.md
```

The epsilon-greedy, LinUCB and Thompson suggestions go through the backend's
outer deterministic guard: only the explicit curated action list is eligible,
MEDIUM-or-higher risk and any PHQ-9 item-9 response bypass the learner, and a
per-user rolling ledger caps exploration at one in five suggestions with no
consecutive exploration. The random row is only a research comparator and must
never be served. Ratings measure bounded pre/post self-report change; skips are
missing outcomes, not failures. No time-in-app, session-duration or streak
signal is in the reward.

Results from the full run are in [`reports/day37-jitai.md`](reports/day37-jitai.md)
and [`docs/evaluation-report.md`](../docs/evaluation-report.md); the framing,
assumptions and non-production boundary are in
[ADR 0037](../docs/adr/0037-personalised-intervention-policy.md). This is not
clinical evidence and is not connected to `GET /plan/today`. Per-user posterior
and exploration state are process-local, and the four response profiles are
hand-authored. Durable state, privacy/safety review, native-language copy review
and a separate product-integration decision are required before any rollout.

## How the pieces fit together

**Config** (`manovia_ml.config`) — YAML, pydantic v2, `extra="forbid"`,
`frozen=True`. Unknown keys are errors; one `seed` reaches every stochastic step
through `manovia_ml.seeds.derive_seed` (sha256 of a label, never `hash()`).

**Data** (`manovia_ml.data`) — the toy fixture is split by a DVC stage, so the
same bytes come out on any machine; `dvc.lock` records the md5 of each output and
the training run records the sha256 of the files it read. A config may pin those
hashes, and a mismatch stops the run with both values printed.

`manovia_ml.data.dvc_lock` answers a narrower question that `dvc status` alone
hides: *is the committed lock honest?* It runs before `dvc repro` (which rewrites
the lock, erasing the evidence) and fails only on a **dependency** whose hash
moved — the splits not being built on this machine is normal and passes. A lock
that names a different version of the code than the commit contains means the
recorded data was not produced by the pipeline in that commit; that is a bug, and
it happened here.

**Tracking** (`manovia_ml.tracking`) — MLflow, local file store by default. MLflow
3.x refuses a filesystem store unless `MLFLOW_ALLOW_FILE_STORE=true`; the package
sets that flag (an explicit opt-out, because one machine writing one record per
run does not need a database) and the store stays `ml/mlruns`. Switch with one
line: `tracking_uri: sqlite:///ml/mlruns/mlflow.db`, or a server URI, or the
compose service in `docker/mlflow/`.

**Reproducibility** — three layers, all tested:

1. `ml/tests/test_reproducibility.py` — same config + seed → metrics equal within
   tolerance; different seed → metrics move on the stochastic model kind.
2. A committed baseline per config (`configs/expected/<name>.json`), checked by
   every run; drift beyond `evaluation.tolerance` fails the run. A seed change
   skips the check rather than failing it, because a seed change is an act, not
   drift.
3. `dvc repro` in CI: the regenerated splits must hash to what `dvc.lock` says.

**Evaluation** (`manovia_ml.evaluation`) — `make ml-eval RUN=<id>` needs only the
run id: the run's own `config.json` says which split and metrics to use. It
re-scores the stored bundle and compares against the metrics the training run
logged, so a moving split or a non-deterministic pipeline fails loudly. (That
check earned its keep on Day 31: it caught a probability-column ordering bug the
first time it ran.)

**Serving** (`manovia_ml.serving`) — a reference path, not a service. A bundle
carries its labels and its library versions; loading it with a different
scikit-learn raises rather than warning.

## Real training runs (Colab/Kaggle)

This sandbox has **no GPU** and no permission to download corpora, so training
code, configs, tests and a tiny smoke run live here; real runs happen on free
Colab/Kaggle. The ready-to-run notebook is
[`notebooks/colab_goemotions_tfidf.ipynb`](notebooks/colab_goemotions_tfidf.ipynb)
and the exact steps are in [`notebooks/README.md`](notebooks/README.md). The
short version:

```bash
pip install -e "./backend[dev]" -e "./ml[ml,dev]"
cd ml && python -m dvc repro
python -m manovia_ml.data.goemotions --download train --split-name train --out data/raw/goemotions/train.jsonl
python -m manovia_ml.training.train --config configs/colab_goemotions_logreg.yaml
python -m manovia_ml.evaluation.evaluate --run <RUN_ID>
```

Bring back the report, the recorded baseline and the run's JSON artefacts. Never
the rows, never `model.joblib` (AGENTS.md, ML ground rule 3).

## The emotion model (Day 32)

`manovia_ml.training.emotion` is one pipeline with five entry points:

| module | what it does |
| --- | --- |
| `taxonomy` | the single documented label-mapping table (GoEmotions/EmoNoBa → Manovia's nine), with a weight per row and a rationale per weight; `neutral` is derived, and GoEmotions' inability to express `loneliness` is a *tested* property, not a footnote |
| `data` | load/convert/check: a conversion reports what it dropped, and the train/held-out check is exact **and** near-duplicate (char-shingle Jaccard, plus a reworded-paraphrase rule behind a word-overlap prefilter) |
| `model` + `tiny` | the LoRA builder and the offline `tiny://xlm-roberta` stand-in (a 2-layer random encoder) that lets the whole path run in seconds on a CPU, with no network |
| `train` | soft targets + `BCEWithLogitsLoss` with per-label `pos_weight`, label smoothing, cosine schedule with warmup, gradient clipping, early stopping with best-adapter restore, AMP only on CUDA, thresholds tuned on the *validation* split, then the artefact + MLflow record + a report and a card |
| `evaluate` | inference only — it **refuses to run in a process that imported the trainer**, so a number can only have come from the saved artefact. Per-label/per-language F1, subset accuracy, ECE, the argmax confusion matrix, row-seeded bootstrap CIs, and a top-20 error analysis grouped by cause |
| `tune` | the ≤12-trial Optuna sweep over LoRA rank and learning rate; each trial is a full run under a nested MLflow run, scored on validation macro-F1 at default thresholds |

```bash
make ml-emotion-smoke                     # tiny, offline, seconds — the CI path
make ml-emotion-train CONFIG=configs/emotion_lora_xlmr.yaml     # GPU
make ml-emotion-eval ARTIFACT=artifacts/emotion/manovia-emotion-xlmr-v1
```

The packaged artefact is a directory (never committed):

```
model/  tokenizer/  thresholds.json  manifest.json  MODEL_CARD.md
```

`thresholds.json` records the split the thresholds were tuned on; `manifest.json`
records the base model, the labels, the data hashes, the seed, the library
versions and the run id; the card is written from
[`templates/MODEL_CARD.md`](templates/MODEL_CARD.md) and always contains the
domain-shift limitation.

## Deliberately not here (yet)

- **No GPU in this sandbox, and the hub is unreachable**, so the real
  `xlm-roberta-base` run happens on Colab/Kaggle
  ([`notebooks/colab_emotion_lora.ipynb`](notebooks/colab_emotion_lora.ipynb)).
  The tiny smoke run is the honest substitute for *plumbing*; it is never a
  quality number, and every report it writes says so.
- **No deployment path.** `manovia_ml.serving` proves a logged model can be
  loaded later; wiring a model into the API is a separate decision with a safety
  argument attached.
- **No network in tests or CI.** Downloads live in explicit, documented steps.

## Known limitations

- The toy model is a canary. On the six-class slice it is at chance on held-out
  rows while fitting its training rows perfectly — expected at 60 training rows,
  documented in [`data/toy/README.md`](data/toy/README.md), and the reason the
  smoke config uses a two-class slice.
- GoEmotions has no `loneliness` class, is Reddit register, and its multi-label
  rows are collapsed by priority. The catalogue and the notebook both say so.
  Day 32's converter (`manovia_ml.training.emotion.data convert`) keeps the raw
  source labels and maps through the weighted table instead; `unreachable_labels`
  in every run manifest names what the corpus cannot teach.
- The Hindi/Bengali side has **no usable dataset today**; the answer is a small
  hand-written, natively reviewed set (plan in the catalogue § "Hindi and Bengali
  emotion data").
- MLflow's file store is in maintenance mode upstream. Fine for one machine; a
  team should switch to SQLite or the service.

## Troubleshooting

| symptom | cause / fix |
| --- | --- |
| `MlflowException: The filesystem tracking backend … is in maintenance mode` | you are running MLflow outside this package (e.g. `mlflow ui` directly): `export MLFLOW_ALLOW_FILE_STORE=true` |
| `dvc repro` fails with `ModuleNotFoundError: manovia_ml` | DVC spawns stages with `python` from `PATH`; run it through `make ml-data`, or activate the venv |
| `config error: … extra_forbidden` | a typo'd key in a config; the message names the key |
| `sha256 mismatch` on a split | the data changed (or was rebuilt with a different seed): rebuild and update the pin deliberately |
| `dvc.lock is out of date` (`make ml-lock`) | a file the pipeline depends on changed without re-running it: `make ml-data`, then commit `dvc.lock` |
| tests all fail with a tracking error | `MLFLOW_TRACKING_URI` points somewhere unwritable; unset it to get the default file store |
