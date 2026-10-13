# Manovia ML platform (`ml/`)

Day 31. This directory is the foundation for every model Manovia might ever put
near a user: a **data catalogue** with licences and verdicts, **experiment
tracking** where every run records enough to be reproduced, **configs** that are
validated rather than convention, and **cards** that have to exist before a
number is quoted.

It does not add a model to the product. Nothing here is wired into the API, and
the crisis gate is untouched (AGENTS.md, ML ground rule 1). What it adds is the
ability to answer, months later, *"where did that number come from, and can we
get it back?"*

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e "./backend[dev]"      # backend pins first (shared deps)
.venv/bin/pip install -e "./ml[ml,dev]"        # this package + mlflow/dvc/sklearn
```

or, from the repository root, `make ml-install`. The package is `manovia-ml`
(module `manovia_ml`); `[ml]` is the ML stack (numpy, scikit-learn, joblib,
pandas, mlflow, dvc) and `[dev]` is pytest/ruff/mypy.

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
| `tests/` | 121 tests, offline, including the reproducibility contract |
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
| `make ml-status` | `dvc status`: is the data what the pipeline recorded? |
| `make ml-catalogue` | check the catalogue (licences, verdict rules, generated table) |
| `make ml-train CONFIG=configs/x.yaml` | train, log the run, check the baseline |
| `make ml-record CONFIG=configs/x.yaml` | re-record that config's baseline (a deliberate act) |
| `make ml-eval RUN=<run id>` | re-score a logged run from its own artefacts |
| `make ml-report RUN=<run id>` | write `reports/<run id>.md` |
| `make ml-smoke` | the CI path end to end |
| `make ml-lint`, `make ml-test` | ruff + ruff-format + mypy; pytest |
| `ml/scripts/fetch_goemotions.sh` | download + convert GoEmotions (network; needs `datasets`) |

## How the pieces fit together

**Config** (`manovia_ml.config`) — YAML, pydantic v2, `extra="forbid"`,
`frozen=True`. Unknown keys are errors; one `seed` reaches every stochastic step
through `manovia_ml.seeds.derive_seed` (sha256 of a label, never `hash()`).

**Data** (`manovia_ml.data`) — the toy fixture is split by a DVC stage, so the
same bytes come out on any machine; `dvc.lock` records the md5 of each output and
the training run records the sha256 of the files it read. A config may pin those
hashes, and a mismatch stops the run with both values printed.

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

## Deliberately not here (yet)

- **No GPU training** and no deep-learning dependency: the first real run is a
  TF-IDF linear model because it is the honest baseline for six classes on a
  small corpus. Transformers belong behind the existing `nlp` extra in the
  backend, with their own ADR.
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
| tests all fail with a tracking error | `MLFLOW_TRACKING_URI` points somewhere unwritable; unset it to get the default file store |
