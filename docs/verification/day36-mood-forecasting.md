# Day 36 verification — mood forecasting with sequence models and honest uncertainty

Run on 2026-10-15 in the pinned Arena checkout `arena/1829c0a6-manovia` (Python 3.11, 2 CPUs). All results below came from commands actually run in this sandbox. The environment has no PyTorch; no model weights or user data were downloaded or committed.

## Research run

Command:

```bash
PYTHON=../.venv/bin/python \
MLFLOW_TRACKING_URI=file:/tmp/manovia-day36-mlruns \
MLFLOW_ALLOW_FILE_STORE=true \
make ml-mood-forecast MOOD_FORECAST_ARGS=--allow-baselines-only
```

Exit 0. Latest MLflow run `94aec4c8845c4768b4138aad7c68299e`; report: [`ml/reports/day36-mood-forecasting.md`](../../ml/reports/day36-mood-forecasting.md). The pipeline was rerun after removing two unused evaluation-config fields; the table's metrics and panel fingerprint are unchanged from the earlier baseline-only run.

| Baseline | MAE | MASE vs seasonal naive | held-out coverage (nominal 80%) | mean width | pinball loss |
| --- | ---: | ---: | ---: | ---: | ---: |
| Persistence | 0.6011 | 0.9936 | 92.31% | 1.8732 | 0.1898 |
| Moving average (7d) | 0.5540 | 0.9154 | 78.32% | 1.6089 | 0.1747 |
| Seasonal naive | 0.6410 | 1.0240 | 92.54% | 1.8686 | 0.1947 |
| Exponential smoothing | 0.5583 | 0.9252 | 77.63% | 1.6356 | 0.1749 |

The intervals were expanded toward nominal 80% on the separate 14-user calibration group, then evaluated on 15 held-out users (1,301 observed targets). Coverage is an empirical aggregate on this synthetic simulator, not an individual guarantee or a formal finite-sample conformal guarantee: rolling-origin windows overlap and repeated target scores are dependent. The report explicitly says **neural result: not measured**: PyTorch was unavailable, so GRU/LSTM/Transformer training, their feature ablations and few-shot adaptation did not run. The moving-average baseline's result is not a neural win or evidence about real Manovia users.

Synthetic panel: 72 users × 210 days, 15,120 calendar rows, 11,812 observed check-ins; SHA-256 `82f956d8a8aef686c7be0e02fe50e6267bd018a28473700d4e430446cc290c3c`. Split: 43 train users, 14 calibration users, 15 held-out test users. Calendar-day target boundaries are start-inclusive/end-exclusive: fit `[0,101)`, validation `[101,130)`, calibration `[130,172)`, test begins at 172. The test pins these boundaries and temporal isolation; unobserved targets are masked, not imputed. No generated panel file or model weights are committed.

A unit test also checked that a baseline-only run refuses to start without the explicit `--allow-baselines-only` override, and inspected the MLflow run for the blocked-torch tag, baseline metric, `not_measured` comparison parameter and summary artifact.

## Backend and API

- `cd backend && ../.venv/bin/pytest -q` → **4504 passed, 4 skipped, 0 failed**.
- `PATH="../.venv/bin:$PATH" make lint` → Ruff clean, **386 files already formatted**, mypy clean (**324 source files**).
- `../.venv/bin/python scripts/export_openapi.py --check` → `api docs up to date`. The generated reference contains 63 operations; its count and byte-for-byte drift are tested.
- A focused run of `tests/privacy/test_export.py`, Outlook/settings/migration integration tests and API-doc tests → **79 passed**.
- Coverage was not measured in this Day-36 `pytest -q` command, and the existing separate safety-module coverage gate was not run. The settings migration was exercised through the repository's SQLite migration tests; this sandbox did not run it against PostgreSQL.

The integration tests cover authentication and terms/privacy consent; default-off and persisted opt-in; 5-, 14- and 40-day histories; duplicate same-day entries; a stale latest check-in; a timezone error; numeric-only inputs with a sentinel note absent from the response; en/hi/bn locale handling; and four synthetic personas (`steady_high`, `steady_low`, `rising`, `work_tagged_low`) passed through response wording checks. API ranges are asserted ordered and explicitly described as **not a calibrated confidence interval or a probability**. The settings preference is present in the owner's `user-settings.json` export, and the archive is now `manovia-export-2`.

## Frontend

Commands run from `frontend/`: `npm run lint`, `npm run typecheck`, `npm run test:run`, `npm run build`, and `npx prettier --check` over every changed frontend file. All exited 0.

Vitest: **74 files passed, 1 skipped; 613 tests passed, 3 skipped**. The Mood Outlook tests cover the opt-in-gated states, server holdback copy, multilingual locale parity, wording and an axe accessibility check. The estimate/range is a semantic table with caption and headers; it is not encoded by color or a chart band. The test run emitted existing React `act(...)`, jsdom `window.scrollTo`, and React Router warnings; they did not fail the suite.

## ML checks and known suite failures

- `PATH="../.venv/bin:$PATH" PYTHON=../.venv/bin/python make lint` → Ruff clean, **146 files already formatted**, mypy clean (**119 source files**).
- `PYTHONPATH=src ../.venv/bin/pytest -q tests/test_config.py::test_every_shipped_config_validates tests/test_mood_forecast.py` → **15 passed, 2 skipped**. The two skips are the optional GRU/LSTM/Transformer fit and few-shot update tests because PyTorch is absent.
- Full `../.venv/bin/pytest -q` → **468 passed, 11 skipped, 2 failed**. The failures are outside Day 36, in the Day-32 optional checkpoint-availability tests: `test_available_baselines_admits_a_checkpoint_it_cannot_reach` cannot import the optional `transformers` package, and `test_checkpoint_is_cached_names_the_checkpoint_and_the_cache` cannot import `huggingface_hub`. No Day-36 test failed. Training/checkpoint dependencies were not installed; in particular, no PyTorch installation was attempted.
- `PYTHONPATH=src ../.venv/bin/python -m dvc repro safety_toy_split` generated the ignored fixture needed by the existing safety-data tests. No tracked DVC data or model artifact was added.

## Required manual follow-up

1. On an approved research machine with the optional training dependencies, run `make ml-mood-forecast` without `--allow-baselines-only`; inspect neural results, held-out coverage/width, feature ablations and guarded adaptation before making any model-quality statement. Do not substitute production histories for the synthetic panel without a separately approved privacy/ethics protocol.
2. Obtain native Hindi and Bengali review for the new copy. `translations/STATUS.md` marks it as draft; the automated locale-parity test is not a language-quality review. Run a manual screen-reader pass for the Outlook table in addition to the passing axe test.
3. Before production rollout, exercise migration `0011_mood_outlook_opt_in` on the supported PostgreSQL version. Confirm wording and eligibility through the existing clinical-review process. Manovia has not completed clinical review and this work is not deployment approval.
