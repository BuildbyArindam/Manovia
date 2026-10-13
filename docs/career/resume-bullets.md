# Resume section — Manovia

> Project line for the resume header:
>
> **Manovia — privacy-first mental-wellbeing self-help companion** (solo,
> 30 days) · FastAPI, Python, React, TypeScript, PostgreSQL ·
> github.com/BuildbyArindam/Manovia

Five bullets. Every number exists in a file in this repository; the mapping
is below the bullets, and nothing here is rounded upward.

## The bullets

1. Built and launched **v1.0.0** of a safety-first mental-wellbeing companion
   (FastAPI + React/TypeScript, English/Hindi/Bengali) solo in 30 days:
   **62 API operations**, an installable offline-capable PWA, and a test
   suite of **4,162 backend and 601 frontend tests** with a branch-coverage
   gate that requires **95% branch coverage on every safety module**
   (measured 100% in the release run).

2. Designed a **rules-first crisis pipeline** — deterministic detection runs
   *before* any LLM call — reaching **1.000 HIGH+IMMINENT recall (95% CI
   [1.000, 1.000])** on a 116-case multilingual dev split and **51/51 on the
   frozen test split (exact 95% lower bound 0.930)**, with the precision
   trade-off measured and documented (0.543) rather than hidden.

3. Built an **offline, reproducible evaluation harness** with bootstrap
   confidence intervals and CI gates across three suites: **60/60** scripted
   chat scenarios pass all six deterministic output-safety checks, **17/17**
   crisis scenarios bypass the LLM and deliver helplines, and the 9-class
   emotion model scores **0.804 macro-F1** over 162 en/hi/bn cases —
   weakening a single crisis pattern fails CI.

4. **Red-teamed my own system** with a 150-case scripted adversarial suite
   (euphemisms, 6 languages, prompt injection, IDOR, jailbreaks): drove the
   score from a **82/140 baseline to 150/150**, fixing a 4,000-character
   truncation bug in risk assessment, ~36 multilingual evasion rules and a
   session-level risk accumulator — each fix with a regression test;
   **18/18 IDOR routes** and **10/10 worst-case jailbreak runs** pass.

5. Engineered **privacy as a feature**: per-field encryption at rest, PII
   redaction before any model call, full export and delete, per-user
   retention, and no raw user text in logs — verified by **29/29 privacy
   red-team cases**, a clean **pip-audit**, gitleaks in CI, and an
   end-to-end test that identifying text never reaches a provider.

## Where each number lives (verification mapping)

| Number | File in this repository |
| --- | --- |
| v1.0.0 | `backend/pyproject.toml`, `frontend/package.json`, `CHANGELOG.md` |
| 30 days | `PROGRESS.md` (Day 1 → Day 30) |
| 62 API operations | `docs/api.md` (header line) |
| 4,162 backend / 601 frontend tests; safety modules at 100% branches, 95% gate | `docs/verification/day30-launch.md` (release-day run); gate thresholds in `backend/scripts/coverage_gate.py` |
| 1.000 recall, CI [1.000, 1.000]; precision 0.543; n=116 dev split | `evals/reports/2026-10-13/summary.md` |
| 51/51 frozen test split, lower bound 0.930 | `evals/reports/safety_2026-10-12_test.md`, cited in `docs/evaluation-report.md` and `README.md` |
| 60/60 scenarios · six checks; 17/17 crisis bypass | `evals/reports/2026-10-13/summary.md` |
| 0.804 macro-F1, 162 cases | `evals/reports/2026-10-13/summary.md` |
| 150/150 final, 82/140 baseline | `evals/reports/redteam/2026-10-13-final/summary.md`, `evals/reports/redteam/2026-10-13-baseline/`, `docs/redteam-report.md` |
| 4,000-character truncation fix, ~36 rules, session accumulator | `docs/redteam-report.md`, `docs/adr/0030-red-team-suite-and-session-risk.md` |
| 18/18 IDOR, 10/10 jailbreaks | `evals/reports/redteam/2026-10-13-final/idor.md`, `docs/redteam-report.md` |
| 29/29 privacy cases | `evals/reports/redteam/2026-10-13-final/summary.md` |
| Clean pip-audit; no-PII-to-provider test | `docs/release-checklist-v1.0.0.md`; `backend/tests` (privacy integration tests) |

## What these bullets deliberately do not say

- No clinical claims: not "detects suicide risk in production", not
  "clinically validated", not "helps users". The datasets are synthetic and
  the clinical review has no sign-offs (`docs/clinical-review.md`).
- No user numbers — there are no users.
- "1.000 recall" is always stated against its dataset, never as an absolute.
