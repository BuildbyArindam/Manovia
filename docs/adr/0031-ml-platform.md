# ADR 0031: ML platform — data catalogue, DVC-versioned data, MLflow tracking and config-driven runs

- Status: accepted (Day 31)
- Date: 2026-10-13
- Deciders: Manovia maintainers
- Supersedes: none. Extends [0006 emotion model](0006-emotion-model.md) (the choice it will supply evidence for) and [0028 model card and clinical review](0028-model-card-and-clinical-review-gate.md)
- Related: [0009 safety ML ensemble](0009-safety-ml-ensemble.md), [0023 multilingual](0023-multilingual-english-hindi-bengali.md), [0024 evaluation harness](0024-evaluation-harness.md), [0030 red team](0030-redteam-session-risk-and-token-families.md), [`ml/README.md`](../../ml/README.md), [`ml/data/CATALOGUE.md`](../../ml/data/CATALOGUE.md)

## Context

Manovia has an emotion model in the product path (0006, 0009) and an evaluation
harness for it (0024), but no answer to three questions that decide whether a
model can be trusted later:

1. **Is this data even ours to use?** Every candidate corpus for emotion or
   mental-health text comes with a licence, and most of them are not commercial:
   EmpatheticDialogues and the IndicNLP suite are CC BY-NC, EmoInHindi is
   CC BY-NC-ND, ESConv is "academic research only", eRisk sits behind a user
   agreement, and several well-known "public" datasets state no licence at all.
   Nothing in the repository recorded which side of that line a dataset was on,
   and a `pip install datasets` line is not a licence review.
2. **Can a number be traced back to what produced it?** The Day-27 model artifact
   needed a whole session of detective work to find out which library versions
   and which data had produced it. A metric in a PR with no run behind it is an
   anecdote.
3. **Hindi and Bengali.** The product is English/Hindi/Bengali (0023). The
   natively reviewed emotion data for those languages may not exist in any usable
   form, and "we will find a dataset later" is how a language quietly becomes
   English-only.

Constraints on the day: no GPU, no permission to download corpora into the
sandbox, and a repository rule that no dataset bytes or model weights may ever be
committed.

## Decision

**`ml/` is a separate installable project at the repository root.** It has its
own `pyproject.toml` (`manovia-ml`, extras `ml` for the ML stack and `dev` for
tests/lint) and shares nothing with the backend's dependency graph. The platform
is a place to do ML work; the product's serving path is untouched by this ADR
(see Consequences).

### 1. The catalogue is the source of truth for data (`ml/data/CATALOGUE.md`)

- One entry per dataset: name, source URL and pinned version, licence, licence
  evidence, allowed use (research/commercial), languages, size, label scheme,
  known biases, whether it contains personal data, a verdict
  (`USE` / `USE WITH CONDITIONS` / `RESEARCH ONLY` / `DO NOT USE`), and the date
  the licence was last verified.
- The entries are machine-readable, and `manovia_ml.data.catalogue --check`
  (run by CI) enforces the rules: a non-commercial or research-only licence can
  never be shown as commercial use; `personal_data: true` can never be plain
  `USE`; a verification older than 180 days fails; a mirror's licence claim is
  not evidence about the original release; and no `DO NOT USE` dataset name may
  appear in `ml/configs/*.yaml`.
- The generated table is part of the check, so the prose and the table cannot
  drift apart.
- **Verdict as of today: 14 entries, 10 restricted.** Usable: GoEmotions
  (Apache-2.0, English), EmoNoBa (CC BY 4.0, Bengali) with conditions, and
  Manovia's own hand-written fixture. Everything else is research-only or
  DO NOT USE.

### 2. No usable Hindi/Bengali emotion data exists for our purpose, so we write it

- Candidates surveyed (emoHind-58K, IndicSentiment/IndicGLUE, EmoInHindi,
  SentiMix Hinglish, Bengali & Banglish) are either non-commercial, adapted from
  product reviews rather than feelings, or translated from English — none is a
  natively authored, commercially licensed Hindi/Bengali emotion corpus.
- The plan is therefore a small hand-written set with a dataset card
  (`ml/data/planned/hi_bn_emotion/DATASET_CARD.md`) and **native review before
  any row is used**, so the sentences are what a speaker would actually type.

### 3. Data is versioned with DVC; hashes travel with every run

- DVC (not git-lfs: it can also express the *pipeline* that produced the splits)
  versions data. `ml/dvc.yaml` defines splitting as a stage, `dvc.lock` records
  the md5 of each output, and no remote is configured — the corpora are not
  redistributable, so the contract is the committed hash, and a team that wants a
  shared cache adds a remote with credentials from the environment.
- A split is rebuilt from the fixture deterministically; `dvc repro && dvc status`
  is a CI gate, so a non-deterministic split fails a pull request.
- Every run logs the **sha256, path, row count and label counts of the exact files
  it read**, and a config may pin those hashes; a mismatch stops the run with both
  values rather than training on different data.

### 4. MLflow tracks runs, with a local file store as the default

- Tracking URI order: the config, then `MLFLOW_TRACKING_URI`, then
  `file:<ml root>/mlruns`. MLflow 3.x puts its filesystem store behind an
  explicit opt-out (`MLFLOW_ALLOW_FILE_STORE`); the package sets it in one module
  (`manovia_ml/mlflow_api.py`) because one machine writing one record per run
  does not need a database, and because `mlflow ui` over a directory is a
  perfectly good UI. `ml/docker/mlflow/docker-compose.mlflow.yml` runs a SQLite
  server for anyone who wants a shared store; nothing depends on it existing.
- **Every run logs seven things, before the caller does anything:** git commit
  (and branch, dirty flag, changed-file count), data hashes and row counts, the
  resolved config plus its sha256 fingerprint (and the YAML itself as an
  artefact), the seed, the library versions that can move a number, the metrics,
  and the artefacts (`model/model.joblib` as a bundle carrying its labels and
  library versions, plus `metrics.json`, `data.json`, `environment.json`).

### 5. Runs are config-driven and reproducible

- Hyperparameters, paths, seeds, metrics and tolerances live in
  `ml/configs/*.yaml`, validated by pydantic v2 with `extra="forbid"` and
  `frozen=True`; an unknown key is an error, and code may not hard-code anything a
  config can express.
- One seed reaches every stochastic step through `manovia_ml.seeds`, by a
  derived, named sub-seed (`sha256("<seed>\x1f<label>")`), never `hash()`.
- A baseline per config (`ml/configs/expected/<name>.json`) holds the metrics,
  run id, seed and tolerance; every run compares itself against it and fails on
  drift beyond the tolerance. A changed seed *skips* the check with a printed
  note — a seed change is an act, not drift. Recording a baseline is a separate
  command (`make ml-record`), so the diff is the review.
- `make ml-eval RUN=<id>` needs only the run id: it re-scores the run's stored
  bundle on the split named in the run's own config and compares the result with
  the metrics the training run logged, so a moving dataset or a non-deterministic
  pipeline fails loudly instead of being averaged away.
- Tests assert the contract: same config + seed → identical metrics (within
  1e-6); different seed → different metrics; a deterministic model kind stays
  deterministic; a drifted baseline is caught; a run's own record contains
  everything needed to repeat it.

### 6. The smoke run is a canary, not a benchmark

- `make ml-smoke` (and CI job `ml-smoke`) runs, in order: `dvc repro` →
  catalogue check → tiny train on the hand-written fixture → baseline comparison
  → report. Seconds, one CPU, no network.
- The fixture is 84 hand-written sentences; every row is distinct, so held-out
  rows share only incidental vocabulary with training rows and the model lands
  below the majority-class rate. That is recorded, not hidden: each run logs
  `majority_accuracy` (the floor a constant classifier scores) and
  `train_accuracy` (whether the pipeline can still fit its own rows), and the
  measured numbers live in `ml/findings/2026-10-13-toy-slice-baselines.md`. The
  smoke gate is reproducibility, not quality, and no number from it may be
  quoted as a result.

### 7. What may be committed

- Committed: code, configs, tests, the catalogue, cards, report templates,
  generated reports, and the hand-written fixture.
- Never committed: dataset bytes, downloaded corpora, `model.joblib`, MLflow's
  store. A CI step fails if a file matching
  `*.bin|*.pt|*.pth|*.safetensors|*.csv|*.parquet|*.joblib|*.pkl|*.h5|*.onnx|*.ckpt`
  is tracked in Git.
- No network in tests or CI; downloads live in explicit documented steps
  (`ml/scripts/fetch_goemotions.sh`, the Colab notebook).

### 8. No GPU here; real runs happen on Colab/Kaggle

- This environment trains on a CPU. Real training is a ready-to-run notebook
  (`ml/notebooks/colab_goemotions_tfidf.ipynb`) with exact instructions in
  `ml/notebooks/README.md`; what comes back is the *report*, the recorded
  baseline and the run's JSON artefacts — never the rows and never the weights.

## Consequences

- **The product is unchanged.** Nothing in `ml/` is imported by the backend, and
  the safety path is untouched: models propose, the deterministic crisis rules
  decide (0008, 0009). Wiring a model into the API is a separate decision with
  its own ADR and its own safety argument.
- **A second Python project to keep in step.** `ml/` pins its own dependencies
  (scikit-learn, mlflow, dvc, pandas); the backend pins others. They are separate
  on purpose — the backend's `scikit-learn` pin should not move because an
  experiment wanted a newer one — but it does mean two upgrade chores.
- **The file store is in maintenance mode upstream.** Fine for one machine; a
  team should switch to SQLite or the compose service, which is one line in a
  config or one environment variable.
- **The catalogue has an expiry date.** Licences get re-verified within 180 days
  or CI fails, and the entries say who verified them. The cost is a chore; the
  alternative is a dataset that quietly becomes unusable after a licence change.
- **The toy fixture cannot show quality.** It shows plumbing. Anyone wanting a
  number about emotion recognition has to run the GoEmotions config on a real
  machine, and the model card (0028) still gates what may be claimed.
- **Hindi/Bengali emotion work starts from zero data.** That is the honest
  position, and it is written down where the next person will look for it.
- **DVC without a remote means the corpus cache is local.** Losing it costs a
  re-download; it never costs correctness, because the hashes are committed.

## Verification

- `cd ml && python -m pytest -q` → **134 passed** (offline, ~9 s), including the
  reproducibility contract, the tracker's seven logged things, the catalogue
  rules and the serving round trip.
- `make ml-smoke` → `dvc repro` up to date, catalogue `14 entries, table
  current, 10 restricted`, train `accuracy=0.375000 log_loss=1.738700
  majority_accuracy=0.500000 train_accuracy=1.000000`, baseline check
  `expected_problems: []`, report written.
- Same config twice: metrics identical run to run (`4a614af4…` and `75653d2d…`).
  `--seed 7`: `accuracy=0.625000 log_loss=1.025380` — the metric moves, and the
  baseline check is skipped with a note.
- `python -m dvc status` → `Data and pipelines are up to date.`
- `python -m manovia_ml.evaluation.evaluate --run <smoke run>` → re-scored the
  stored bundle and reported "this run's metrics match the training run's logged
  metrics".
- `git ls-files | grep -E "\.(bin|pt|safetensors|csv|parquet)$"` → empty.
- CI job `ml-smoke` (`.github/workflows/ci.yml`) runs the same commands plus the
  tracked-artefact check on every pull request.
