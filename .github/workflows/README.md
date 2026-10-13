# Workflows

- [`ci.yml`](ci.yml) — Manovia CI (Day 7): backend (ruff, ruff-format, mypy
  strict, pytest with an 80 % coverage gate), frontend (ESLint, `tsc`, Vitest,
  production build), gitleaks secrets scan, and a **report-only** dependency
  audit (pip-audit + `npm audit --omit=dev`) whose findings land in the job
  summary and as warning annotations without ever failing the check. Runs on
  pushes to `main` and on every pull request; jobs mirror the local
  `make lint` / `make test` targets.
- [`ci.yml`](ci.yml) `ml-smoke` job (Day 31): installs `./ml[ml,dev]`, runs
  `dvc repro` + `dvc status` (the regenerated splits must hash to what
  `dvc.lock` recorded), checks `ml/data/CATALOGUE.md` (licences, verdicts and
  the generated table), runs `make smoke` (toy train → committed-baseline
  comparison → report), then the ML test suite, ruff/ruff-format/mypy, and a
  check that no dataset or model-weight file is tracked in Git. CPU-only,
  offline, ~2 minutes; job summary carries the smoke report.
- [`evals.yml`](evals.yml) — Evaluation Harness (Day 24): runs weekly on
  Mondays at 04:00 UTC (`schedule`), on pull requests touching `evals/` or
  safety/NLP/chat/content code, and via `workflow_dispatch`. Executes
  `make eval` (`evals/run_all_evals.py --provider fake --with-judge`), enforces
  the CI gates in `evals/thresholds.yaml`, uploads `evals/reports/<date>/`
  (`summary.md`, `report.html`, `summary.json`, per-suite reports) as a
  workflow artifact, appends to `$GITHUB_STEP_SUMMARY`, and posts/updates a
  sticky PR summary comment (`<!-- manovia-eval-summary -->`).
