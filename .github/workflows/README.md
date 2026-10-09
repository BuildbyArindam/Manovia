# Workflows

- [`ci.yml`](ci.yml) — Manovia CI (Day 7): backend (ruff, ruff-format, mypy
  strict, pytest with an 80 % coverage gate), frontend (ESLint, `tsc`, Vitest,
  production build), gitleaks secrets scan, and a **report-only** dependency
  audit (pip-audit + `npm audit --omit=dev`) whose findings land in the job
  summary and as warning annotations without ever failing the check. Runs on
  pushes to `main` and on every pull request; jobs mirror the local
  `make lint` / `make test` targets.
