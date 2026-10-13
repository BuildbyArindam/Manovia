# Day 28 verification: documentation, model card and portfolio polish

Date: 2026-10-13. Branch: `arena/63972da8-manovia`, based on `9032325`.
Scope: documentation and portfolio. No new features. Application-code changes:
the `health` router tag (for the API reference); removal of the GET chat stream
route (a maintainer decision, see below); and two tests that close the safety
branch gap. Each was made as a separate, reviewable change.

## Final results

| Check | Command (from the stated directory) | Result |
| --- | --- | --- |
| Backend lint | `ruff check .` (`backend/`) | All checks passed |
| Backend format | `ruff format --check .` | 340 files already formatted |
| Backend types | `mypy app tests` | No issues in 282 source files |
| Backend tests | `pytest --cov=app --cov-branch` (`backend/`, run with `env -i`) | **3923 passed, 3 skipped**, exit 0, 404 s, 49 warnings (not investigated) |
| Coverage gate | `python scripts/coverage_gate.py <coverage json>` | **Exit 0, all rules satisfied.** Overall statements 96.56%, branches 89.95% (floor 85%). Every `app/services/safety` module 100% statements and branches (floor 95%). Crypto and scoring modules 100% |
| API reference | `python scripts/export_openapi.py --check` | "api docs up to date" (62 operations) |
| API and chat tests | `pytest tests/chat/test_chat_api.py tests/unit/test_api_docs_export.py tests/integration/test_openapi.py tests/integration/test_health.py` | 76 passed |
| Frontend lint | `npm run lint` | Exit 0 |
| Frontend format | `npx prettier --check .` (`frontend/`) | Exit 0 |
| Frontend types | `npm run typecheck` | Exit 0 |
| Frontend tests | `npm run test:run` | **600 passed, 3 skipped** (73 files passed, 1 skipped) |
| Tables | `/tmp/tablecheck.py` on `docs/model-card.md`, `docs/clinical-review.md`, `docs/api.md`, this note | 0 problems each |
| Links | Relative Markdown links in every `.md` file | 0 broken, excluding one table-pipe artifact in `PROGRESS.md` (`r\|light`) that predates Day 28 |

Test count: the Day 27 record said 3906. The Day 28 baseline was 3910. Day 28
added nine API-docs tests and two safety tests, which gives 3921. The final run
shows 3923, so two tests are not accounted for yet.

## Failures found and how they were fixed

1. **Safety branch gate failed (91.67% against 95%).** The gaps were
   `ensemble.py` lines 253 to 258 (`decide()` with a disabled classifier, which no
   test called) and `patterns.py` line 480 (the loader's "not a mapping" error).
   Fixed by adding two tests in `tests/safety/test_loader_and_engine_branches.py`.
   The floor was not changed, on the maintainer's instruction.
2. **The GET chat stream route** (`/api/v1/chat/sessions/{id}/stream?message=`)
   put the message in the URL. The maintainer chose to remove it. The web client
   already used POST. The route, its documentation (the chat module docstring,
   the export script, `docs/api.md`, ADR 0011 with a dated amendment) and its
   tests were changed. The ownership test no longer sends a GET, and a new test
   asserts that GET on the stream path returns 405 and does not echo the message.
3. **Operation count** pinned at 63 in `test_api_docs_export.py`. Now 62, with a
   comment that says why.
4. **Prettier** failed on 18 frontend files, and then on `frontend/README.md`
   (line wrapping only). Fixed.

Lint and type checks on the test and script files changed by these fixes are clean.

## Corrections made on Day 28

Each of these was checked against the file in the right-hand column.

| Was | Now | Source checked |
| --- | --- | --- |
| Old model card named OpenAI as a provider | Provider list is `anthropic`, `ollama`, `fake` | `backend/app/core/config.py` |
| README badge "backend coverage 100%" | Measured statements and branches, green, with the gate status stated | `coverage_gate.py` output |
| "verified Indian helplines" (evaluation report §5 twice, README) | 14416 and 112 are not flagged; KIRAN is flagged `needs_verification` | `backend/app/content/helplines.json` |
| Privacy doc "verified crisis helplines" (twice) | "schema-checked, not call-verified" | `ethics-and-limitations.md` line 89 |
| Model card: domain-shift "38 of 39 wrong" | 37 of 39 (accuracy 0.051 on 39 items is 2 correct) | `evals/reports/2026-10-13/emotion.md` |
| Model card: "mostly by labelling them neutral" | In the report's listed examples, 11 of 14 wrong predictions are `neutral` | `emotion.md` failure list |
| Model card F12: "40–50 items per emotion-language cell" | 5 to 8 items across 27 cells | `evals/datasets/emotion_cases.jsonl` (162 rows) |
| Model card C3: ensemble "tested" | Partly tested; its module's branch gate was failing until the two tests | coverage JSON, ADR 0029 |
| CHANGELOG "demo assets under docs/assets/" | Removed: no demo assets exist | `ls docs/assets` |
| CHANGELOG "safety-design and ethics finalised" | Removed: not reviewed in this pass | — |
| Architecture: "schema-checked" helplines; offline dialog "when saved" | "validated at load"; the dialog says "the last time you were online" | `helplines.json` loader; `frontend/src/lib/i18n/locales/en.json` |
| PR template claimed as new | It already existed (`d998451`). The existing file was kept; the claim holds | `git show HEAD:.github/pull_request_template.md` |

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

- The helpline entries flagged `needs_verification` have not been called. See
  [manual tests](day28-manual-tests.md), section 7.4.
- The Hindi and Bengali copy has not been reviewed by a native speaker.
- The emotion analyser's RoBERTa checkpoint was not measured on this data. The
  card says so.
- Screenshots were not regenerated. The Day 25 images are unchanged.
- Docker compose, `make smoke`, `make prod-up`, the browser e2e, Lighthouse and
  the bundle budget were **not run** in this sandbox. See
  [manual tests](day28-manual-tests.md).
- The Mermaid sequence diagrams in `docs/architecture.md` were written by hand and
  not rendered here (no `mmdc` in the sandbox). They need a visual check on GitHub.
- The two unexplained tests in the count above.
- CI does not run the branch gate (it runs an 80% statement floor). The gate is
  enforced only by `make test`.
