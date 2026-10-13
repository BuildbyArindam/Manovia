# Day 28 verification: documentation, model card and portfolio polish

Date: 2026-10-13. Branch: `arena/63972da8-manovia`, based on `9032325`.
Scope: documentation and portfolio only. No new features. The only
application-code change is the `health` router tag (for the API reference).

## Commands and results

| Check | Command (from the stated directory) | Result |
| --- | --- | --- |
| Backend lint | `ruff check .` (`backend/`) | All checks passed |
| Backend format | `ruff format --check .` | 340 files already formatted |
| Backend types | `mypy app tests` | No issues in 282 source files |
| Backend tests | `pytest --cov=app --cov-branch` (`backend/`, run with `env -i`) | **3921 passed, 3 skipped**, exit 0, 400 s, 49 warnings (not investigated) |
| Coverage gate | `python scripts/coverage_gate.py /home/user/cov-final.json` | **Exit 1.** Overall statements 96.55% and branches 89.87% pass the 85% floor. Safety group branches **91.67%** fail the 95% floor |
| API reference | `python scripts/export_openapi.py --check` | "api docs up to date" |
| API tests | `pytest tests/unit/test_api_docs_export.py tests/integration/test_health.py tests/integration/test_openapi.py` | 18 passed |
| Frontend lint | `npm run lint` | Exit 0 |
| Frontend format | `npm run format:check` | Failed on 18 files (line wrapping). Fixed with `prettier --write`, committed. Now passes |
| Frontend types | `npm run typecheck` | Exit 0 |
| Frontend tests | `npm run test:run` | **600 passed, 3 skipped** (73 files passed, 1 skipped) |
| Tables | `/tmp/tablecheck.py` on `docs/model-card.md`, `docs/clinical-review.md`, `docs/api.md` | 0 problems each |
| Links | Relative Markdown links in all 131 `.md` files | 0 broken, excluding one table-pipe artifact in `PROGRESS.md` (`r\|light`) that predates Day 28 |

The backend total is 3921 against the 3910 baseline, which was run before the
nine new API-docs tests. The 2-test gap is not explained yet. The Day 27 record
said 3906. Quote 3921 as the Day 28 figure.

## The coverage gate

The gate fails on the Day 28 tree, and it failed on the baseline run for the same
reason. The two uncovered locations are:

- `app/services/safety/ensemble.py`, branch 253 to 258: the `decide()` path where
  the classifier is disabled. No test calls `decide()` with a disabled classifier.
  `tests/safety/test_loader_and_engine_branches.py` only covers the exploding
  classifier.
- `app/services/safety/patterns.py`, line 480: the "expected a mapping at the top
  level" error in `_load_yaml`. No test feeds it a non-mapping YAML file.

The two missing tests are small. Under the Day 28 scope they are not written
here. The choice between adding them and changing the floor is open. See
[ADR 0029](../adr/0029-branch-coverage-gates.md).

CI runs `pytest --cov-fail-under=80` (statements only) and does not run the
branch gate. The README badge, the CHANGELOG and PROGRESS now say the gate is
failing.

## Corrections made on Day 28

Each of these was checked against the file named in the right-hand column.

| Was | Now | Source checked |
| --- | --- | --- |
| Old model card named OpenAI as a provider | Provider list is `anthropic`, `ollama`, `fake` | `backend/app/core/config.py` |
| README badge "backend coverage 100%" | Measured statements and branches, labelled yellow, gate status stated | `cov-final.json`, `coverage_gate.py` |
| "verified Indian helplines" (evaluation report §5 twice, README) | 14416 and 112 are not flagged; KIRAN is flagged `needs_verification` | `backend/app/content/helplines.json` |
| Privacy doc "verified crisis helplines" (twice) | "schema-checked, not call-verified" | `ethics-and-limitations.md` line 89 |
| Model card: domain-shift "38 of 39 wrong" | 37 of 39 (accuracy 0.051 on 39 items is 2 correct) | `evals/reports/2026-10-13/emotion.md` |
| Model card: "mostly by labelling them neutral" | In the report's listed examples, 11 of 14 wrong predictions are `neutral` | `emotion.md` failure list |
| Model card F12: "40–50 items per emotion-language cell" | 5 to 8 items across 27 cells | `evals/datasets/emotion_cases.jsonl` (162 rows) |
| Model card C3: ensemble "tested" | Partly tested; the module's safety branch gate fails | coverage JSON, ADR 0029 |
| CHANGELOG "demo assets under docs/assets/" | Removed: no demo assets exist | `ls docs/assets` |
| CHANGELOG "pull request template" | Already present (`.github/pull_request_template.md`, from commit `d998451`); the claim holds. Not overwritten | file present in `HEAD` |
| CHANGELOG "safety-design and ethics finalised" | Removed: not reviewed in this pass | — |
| Architecture: "schema-checked" helplines; offline dialog "when saved" | "validated at load"; the dialog says "the last time you were online" | `helplines.json` loader; `frontend/src/lib/i18n/locales/en.json` |

## Claims checked and kept

- Model card: rules-only dev recall CI [0.204, 0.471] and precision 0.810
  (`evals/reports/2026-10-13/safety.md`).
- Chat rubric per language en 26, hi 18, bn 16, total 60 (`chat_rubric.jsonl`).
- Emotion dataset en 72, hi 45, bn 45; 123 in-domain and 39 domain shift; nine
  emotions, 18 items each across languages (`emotion_cases.jsonl`).
- The LLM chain ends in the canned reply (`backend/app/services/llm/__init__.py`).
- The frozen test split was scored on 2026-10-10 and 2026-10-12. Both reports
  exist (`evals/reports/safety_2026-10-10_test.md`, `safety_2026-10-12_test.md`)
  and give the same headline figures: ensemble recall 1.000, precision 0.567;
  rules only recall 0.255, precision 0.812.
- GoEmotions: 27 emotion categories plus Neutral, from English Reddit comments
  (arXiv 2005.00547, fetched).
- "Day 20" as the label for the screening section matches ADR 0020.
- No hard-coded model identifier (`backend/tests/llm/test_no_hardcoded_model.py`).
- The log-redaction end-to-end test exists
  (`backend/tests/integration/test_log_redaction_e2e.py`).
- `dangerouslySetInnerHTML` appears nowhere in `frontend/src` (grep: 0).
- The production compose file requires `SECRET_KEY` and `FIELD_ENCRYPTION_KEY`.
- Private vulnerability reporting is disabled (`{"enabled":false}`).

## Still unverified or open

- The chat-stream query-string issue is open and not fixed. Decision needed.
- The helpline entries flagged `needs_verification` have not been called. See
  [manual tests](day28-manual-tests.md), section 7.4.
- The Hindi and Bengali copy has not been reviewed by a native speaker.
- The emotion analyser's RoBERTa checkpoint was not measured on this data. The
  card says so.
- Screenshots were not regenerated. The Day 25 images are unchanged.
- Docker compose, `make smoke`, `make prod-up`, the browser e2e, Lighthouse and
  the bundle budget were **not run** in this sandbox. See
  [manual tests](day28-manual-tests.md).
- The 2026-10-10 safety run is a second scoring of the test split, which the card
  now states.
- The `/home/user/cov-final.json` coverage file is outside the repository. The
  gate result above can be reproduced by re-running the backend test command.
