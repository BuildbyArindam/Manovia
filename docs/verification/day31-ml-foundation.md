# Day 31 verification — ML foundation (2026-10-13)

Every command below was really run in the Day 31 sandbox, on the final Day 31
tree, with the repository's own virtualenv (`.venv`, Python 3.11.2). The work is
in [PR #38](https://github.com/BuildbyArindam/Manovia/pull/38) (not merged). The sandbox
has no GPU, no Docker, and can reach only github.com / PyPI / npm, so three
checks are adapted or performed by the owner; each is stated where it happens.

## PASS/FAIL table

| # | Check (from the Day-31 brief) | Result | Evidence |
| --- | --- | --- | --- |
| 1 | `pip install -e ".[ml]"` succeeds | **PASS** | From `ml/`: `../.venv/bin/python -m pip install -e ".[ml]"` → `Successfully installed manovia-ml-0.1.0`. The repository root has no `pyproject.toml` (the backend and the ML package are separate projects by design), so the root form is `pip install -e "./ml[ml]"`; both are documented. Installing with `[ml,dev]` pulls mlflow 3.17.1, dvc 3.67.1, scikit-learn 1.9.1, pandas 3.0.6, numpy 2.4.6. |
| 2 | `make ml-smoke` logs to MLflow with run id + params shown | **PASS** | `make ml-smoke` → `dvc repro` up to date → catalogue check `14 entries, table current, 10 restricted` → `SMOKE RUN_ID=b6b770d57dd7461fbfab2055f23a4eea` → report written → JSON tail: config fingerprint, train/eval sha256, metrics, seed. MLflow store: `ml/mlruns` (file store, project default). Params/metrics readable with `MlflowClient().get_run(<id>).data.params/metrics`. |
| 3a | Same seed twice → metrics within tolerance | **PASS** | Two runs of `configs/toy_emotion_sgd.yaml` (runs `4a614af4…`, `75653d2d…`): `accuracy=0.375000 log_loss=1.738700 macro_f1=0.272727 majority_accuracy=0.500000 train_accuracy=1.000000 weighted_f1=0.272727` — identical, and within the recorded 1e-6 tolerance of the committed baseline (`expected_problems: []`). |
| 3b | Changed seed → metrics differ | **PASS** | `--seed 7`: `accuracy=0.625000 log_loss=1.025380 macro_f1=0.619048`. The run prints that it skipped the baseline check, because the baseline was recorded at seed `20261013` — a seed change is an act, not drift. The automated version of 3a/3b is in `ml/tests/test_reproducibility.py` (134 tests, all passing). |
| 4a | `dvc status` clean | **PASS** | `cd ml && python -m dvc status` → `Data and pipelines are up to date.` |
| 4b | Dataset hash visible in a run | **PASS** | Each run logs `data.train.sha256`, `data.train.rows`, `data.eval.sha256`, `data.eval.rows` as params and writes `data.json`; the smoke run's report carries `aa4a9871…` (20 rows) and `055a7a9f…` (8 rows) in its Data table. `dvc.lock` holds the md5 of the same files (stage `toy_split` / `toy_smoke_split`). |
| 5 | `git ls-files \| grep -E "\.(bin\|pt\|safetensors\|csv\|parquet)$"` empty | **PASS** | Empty (exit 1 from grep, no output). CI generalises it to `*.bin,*.pt,*.pth,*.safetensors,*.csv,*.parquet,*.joblib,*.pkl,*.h5,*.onnx,*.ckpt`, and the ML `.gitignore` adds the same belt for anything untracked. `dvc.lock` + the fixture are the only data-shaped commits. |
| 6 | CI `ml-smoke` job passes locally with `act` or by running its commands | **PASS, and green on GitHub Actions** | `act` needs Docker, which the sandbox does not have, so the job's steps were first run in order against a **fresh clone with a fresh virtualenv** — install → `dvc repro` + `dvc status` → `catalogue --check` → `make smoke` → `pytest -q` (134 passed) → `ruff`/`ruff format`/`mypy` → the two Git guards. Then the real thing: [run 38383897480](https://github.com/BuildbyArindam/Manovia/actions/runs/38383897480) on commit `cd20772` — **`ML — smoke, reproducibility, catalogue: success` in 1m34s**, alongside backend (9m17s), frontend, gitleaks and the dependency audit, all green. The first execution (run 38382218135) **failed**, and finding out why was the most useful thing that happened all day (fixes 5 and 6 below). |
| 7 | Print CATALOGUE.md highlighting licence concerns | **PASS** | `python -m manovia_ml.data.catalogue --check` → `catalogue ok: 14 entries, table current, 10 restricted (emoHind-58K, IndicSentiment / IndicGLUE sentiment, EmoInHindi, SentiMix Hinglish, Bengali & Banglish, EmpatheticDialogues, ESConv, MentalChat16K, eRisk/DepreSym, Dreaddit/SMHD)`. Highlights: GoEmotions is Apache-2.0 (**USE**); emoHind-58K and EmoNoBa are CC BY 4.0 web text (**USE WITH CONDITIONS**); IndicGLUE/IndicSentiment (CC BY-NC 4.0) and EmoInHindi (CC BY-NC-ND 4.0) are **RESEARCH ONLY**; EmpatheticDialogues (CC BY-NC), ESConv (academic-only), eRisk (user agreement), MentalChat16K (real clinical transcripts) and Dreaddit/SMHD are **DO NOT USE**. No usable natively authored, commercially licensed Hindi/Bengali emotion corpus exists; the plan is a hand-written set with native review. |
| 8 | ML test suite and linters | **PASS** | `cd ml && python -m pytest -q` → **134 passed in 8.6 s**, fully offline (temporary `file:` MLflow store per test, splits built into tmp dirs, `-m 'not network'`). `ruff check .` "All checks passed!", `ruff format --check .` "41 files already formatted", `mypy src tests` "no issues found in 32 source files". |
| 9 | Full repository lint and tests (wrap-up requirement) | **PASS** | `make lint` (backend + frontend): ruff clean, `355 files already formatted`, mypy clean in 295 files, eslint clean. `make test`: backend **4159 passed, 3 skipped** (skips = uninstalled optional `nlp`/`llm` extras), coverage 96%; frontend **601 passed, 3 skipped** (74 files). |
| 10 | Real `make ml-eval RUN=<id>` (added scope, never run before Day 31) | **PASS** | `python -m manovia_ml.evaluation.evaluate --run 4a614af45cb041959d14b306a1ec49be` → `EVAL_RUN_ID=36af3ca90fa64daeaf7adbcf631ac697`, `scored run=4a614af4… config=toy_emotion_sgd`, `split: data/processed/toy_smoke_eval.jsonl rows=8 sha256=055a7a9f5de2…`, metrics matching the training run, and `consistency: this run's metrics match the training run's logged metrics` (exit 0). |
| 11 | Owner-only steps (cannot be done here) | **N/A — listed** | (a) the first real GoEmotions run on Colab/Kaggle (`ml/notebooks/README.md` steps 4–8); (b) the first GitHub Actions execution of `ml-smoke` (needs push/PR); (c) Docker-based checks: `make ml-mlflow-up` and the Docker quick start; (d) a browser check of `mlflow ui` against a run. |

## One check that failed and could not be explained from here

The `Accessibility & Lighthouse` job failed on this PR's runs 38382218185 and
38385043567 (step: Playwright — axe, targets, keyboard, zoom, offline,
installability), exit 1. `main`'s run at 09:16 the same day was green, and so was
the Day-30 PR's. This diff does not touch `frontend/` or `backend/` at all
(`git diff origin/main...HEAD -- frontend/ backend/` is empty), so the code that
job builds and drives is identical to a commit where it passed; the sandbox
cannot read the job's logs (the log and artifact hosts are outside its network
allowlist) and cannot re-dispatch the workflow (needs `actions: write`).
Recorded in PROGRESS.md § Known issues with the owner action: press **Re-run
jobs** on that job, and if it fails again read the
`day25-a11y-lighthouse-reports` artifact.

## Fixes made during verification (root causes, not symptoms)

1. **Baselines were written into the repository.** `expected_metrics_path()`
   derived the path from the config's *name* and always resolved into
   `ml/configs/expected/`, so any run whose config lived elsewhere (a test, a
   Colab copy, `/tmp`) left files in the checkout — two had already leaked. Now
   the baseline lives in `expected/` beside its config; two tests pin both the
   temporary-config case and the `ml/configs/expected/` convention.
2. **The report step of `make ml-smoke` died in a fresh process.** MLflow 3.x's
   file-store opt-out lived in `tracking.py`, which the reporting process never
   imports — the first end-to-end attempt trained fine (run `224b0438c52a…`) and
   then hit the maintenance-mode error while writing its report.
   The opt-out now lives in `mlflow_api.py`, the single module that imports
   MLflow for this package, with a regression test that runs a fresh interpreter
   with `MLFLOW_ALLOW_FILE_STORE` removed from the environment.
3. **A claim the measurements did not support.** The smoke config said the binary
   slice "has a real signal"; it does not (0.375 against a 0.500 majority-class
   floor, while fitting its training rows perfectly). Fixed at the root by
   logging `majority_accuracy` and `train_accuracy` on every run, writing the
   numbers down in `ml/findings/2026-10-13-toy-slice-baselines.md`, and correcting
   every file that repeated the claim. The smoke gate is reproducibility, not
   quality — and `train_accuracy = 1.000` is the canary that a broken pipeline
   would move.
4. **The backend's hard-coded-model-id guard caught the catalogue.** The
   MentalChat16K entry named the model that generated its synthetic half; the
   guard (rightly) only allows vendor model ids in `.env.example`. The entry now
   describes the generator and points at the source paper — the guard was not
   widened.
5. **The repository was missing four modules of its own package, and only CI
   noticed.** The first `ml-smoke` execution died at `dvc repro` with
   `[Errno 2] No such file or directory: .../ml/src/manovia_ml/data/toy.py`. The
   root `.gitignore` has a bare `data/` rule (Day 13's vector store) which matches
   *any* directory named data, so it ignored `ml/src/manovia_ml/data/` — four
   modules of `manovia-ml`. `git add ml/src` skipped them silently; they existed
   on this machine and in no clone. The Day-31 re-inclusion covered `ml/data/`
   (catalogue, fixture) and that was all. Fixed with `!ml/src/manovia_ml/data/`
   and a new CI step that fails if any `.py` file under `ml/` is untracked.
6. **The same rule was hiding those files from the lint gate.** Ruff respects
   `.gitignore`, so `ruff check .` had been reporting "All checks passed!" about
   a module it never read. Re-checked in a fresh clone, the four files had four
   errors (two long lines, a `noqa` naming a rule this project does not enable,
   an ambiguous `×` in a docstring); all four are fixed. "It is not in Git" and
   "it is not linted" turn out to be the same sentence.

The lesson is recorded where it will be needed again: the CI job asserts Git
completeness before it asserts anything else, because a check that skips a file
is indistinguishable from a check that passes.

## Reproduce any of this

```bash
python -m venv .venv
.venv/bin/pip install -e "./backend[dev]" -e "./ml[ml,dev]"
make ml-smoke                      # the CI path, seconds, offline
git clone --branch arena/020f8dea-manovia <repo> /tmp/fresh && cd /tmp/fresh/ml && \
  python -m venv /tmp/fresh-venv && /tmp/fresh-venv/bin/pip install -e ".[ml,dev]" && \
  /tmp/fresh-venv/bin/python -m dvc repro   # what CI actually does, from nothing
cd ml && python -m pytest -q       # 134 tests
cd ml && python -m dvc status      # Data and pipelines are up to date.
make ml-train CONFIG=configs/toy_emotion_multiclass_logreg.yaml
make ml-eval  RUN=<run id from the line above>
make ml-report RUN=<run id>
```
