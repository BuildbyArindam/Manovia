# Day 30 verification — launch, v1.0.0 (2026-10-13)

All commands were really run in the Day 30 sandbox on the final Day 30 tree.
The sandbox has no Docker and can reach only github.com / PyPI / npm, so the
Docker-stack and browser-download steps were adapted; every adaptation is
stated where it happened.

## PASS/FAIL table

| # | Check | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Fresh clone in a temp dir → `cp .env.example .env` → stack up → `scripts/smoke.sh` | **PASS (adapted: no Docker)** | `git clone` into `/tmp/fresh`, `cp .env.example .env`, fresh venv install, `alembic upgrade head`, uvicorn on :8001, `npm ci` + `npm run build` + `vite preview` on :3001, then `SMOKE_SKIP_COMPOSE=1 ./scripts/smoke.sh` → **`SMOKE PASS: API ready, guest created, consent recorded, frontend HTTP 200`**. Found a real bug on the first attempt (see "Fix made during verification"). `docker compose up -d --build` itself needs a Docker machine (owner step). |
| 2a | `make lint` | **PASS** | Backend: `ruff check` "All checks passed!", `ruff format --check` "355 files already formatted", `mypy app tests` "no issues found in 295 source files". Frontend: `eslint .` clean, Prettier "All matched files use Prettier code style!", `tsc --noEmit` clean. |
| 2b | `make test` | **PASS** | Backend: **4160 passed, 3 skipped** (exit 0; skips are the uninstalled optional `nlp`/`llm` extras). Coverage: **statements 96.70%, branches 90.33%**; coverage gate "all rules satisfied" — every `app/services/safety` module 100% branches except `rules.py` at 98.84% (floor 95%). Frontend: **601 passed, 3 skipped** (74 files). |
| 2c | `make eval` | **PASS** | Overall CI gate **PASS**. Safety: ensemble HIGH+IMMINENT recall **1.000** [1.000, 1.000], precision 0.543 [0.441, 0.645]; rules-only recall 0.333 (floor 0.330); per-language recall 1.000; 0 regressions. Emotion: macro-F1 **0.804** [0.739, 0.853] (n=162; bn floor 0.774 ≥ 0.450). Response: **60/60** all six checks, crisis 17/17. Rewritten report files differed only in the judge-disabled line and timestamps and were reverted, as on Day 29. |
| 3 | Full user journey against the live stack | **PASS 11/11 (adapted: HTTP, not Playwright)** | Playwright's browser download is blocked in the sandbox (cdn.playwright.dev unreachable; no system Chromium). The journey ran as scripted HTTP against the fresh-clone stack — step table below. Real-browser E2E (axe, keyboard, zoom, offline, install) runs in CI and is green on `main` (run 38368319863). |
| 4 | Helplines `last_verified` within 30 days | **PASS** | All 35 entries: `2026-10-09`, 4 days old. Not re-dated: the sandbox cannot reach helpline sources, and re-dating without checking would fabricate verification. 4 entries remain `needs_verification` — owner must phone-verify. |
| 5 | "Not a clinical service" / Beta labels in UI and README | **PASS** | Header Beta badge + footer "Portfolio demo — not a clinical service. Clinical review is not complete." (en/hi/bn) asserted by the new test in `frontend/src/App.test.tsx` (runs green); README opens with the same statement (first body line). |
| 6 | Every resume number maps to a repo file | **PASS** | The mapping table inside `docs/career/resume-bullets.md` lists each number with its source file; spot-checked: 62 operations (`docs/api.md` line 5), 4160/601 tests (this note), recall/precision/macro-F1 (`evals/reports/2026-10-13/summary.md`), 150/150 + 82/140 (`docs/redteam-report.md` §total), 51/51 + 0.567 (`evals/reports/safety_2026-10-12_test.md`), [0.930, 1.000] (`docs/model-card.md`), 18/18 (`evals/reports/redteam/2026-10-13-final/idor.md`), 29/29 (`.../summary.md`). |
| 7 | Release notes + version tag | **PASS (prepared)** | Notes: `docs/releases/v1.0.0.md` (features, safety statement, eval numbers, limitations). Version 1.0.0 in `backend/pyproject.toml`, `frontend/package.json`, `API_VERSION`, Sentry release, regenerated `docs/openapi.json`/`docs/api.md` (drift tests pass). The tag itself must be pushed from merged `main` by the owner — exact steps in `docs/release-checklist-v1.0.0.md` §2. |
| 8 | Release checklist extras | **PASS / owner steps** | CI all green on `main` (runs 38368319917 + 38368319863: backend, frontend, dependency audit, gitleaks, a11y+Lighthouse all `success`). `pip-audit`: no known vulnerabilities. `npm audit --omit=dev --audit-level=high`: exit 0 (2 moderates in react-router 6.x, known since Day 22). Secrets: offline stand-in 42 hits, all `generic-api-key` on source prose; high-signal grep clean. `gh repo edit` returned HTTP 403 for the sandbox token → description/topics/Discussions/pin/social-preview are owner steps (commands in the checklist); the social-preview image is committed. |

## User journey step results (fresh-clone stack, scripted HTTP)

| Step | Result | Detail |
| --- | --- | --- |
| 1. Guest onboarding (guest → consents → me) | PASS | 201 / 201 / 200 |
| 2. Chat, benign message | PASS | 200; `response_type: check_in`, `llm_called: true` — note below |
| 3. Crisis test phrase | PASS | 200; `risk_level: high`, `response_type: crisis`, **`llm_called: false`**, helplines in the reply; log carries only `text_sha`, never text |
| 4. Mood check-in + streak | PASS | 201 / 200 (first attempt 422 was this script's own invalid tag, `sad` vs `sadness`) |
| 5. Journal write + read back | PASS | 201 / 200, body round-trips decrypted |
| 6. Exercise list + complete | PASS | 200 / 201 (`body-scan`) |
| 7. CBT thought record | PASS | 200 |
| 8. PHQ-9 submit, item 9 positive | PASS | 201; log: `item9_flagged: true, crisis: true`; support options in the response |
| 9. Insights weekly | PASS | 200 |
| 10. Export everything | PASS | 200; ZIP with 13 files; the journal body is present decrypted |
| 11. Delete account | PASS | 200; `GET /auth/me` afterwards → 401 |

Journey notes, stated honestly:

- The journal needs `FIELD_ENCRYPTION_KEY` (503 `encryption_unavailable`
  without one, fail-closed by design). The Docker dev stack injects a dev
  default (`docker-compose.yml`); the bare-metal run had to set the same
  value in `.env`. The README's no-Docker quick start does not mention this —
  recorded as a known issue.
- Known ML over-escalation reproduced: the first benign phrasing tried
  ("I've been feeling overwhelmed at work lately.") drew the crisis card
  (`risk_level: high`), and even "I had a calm evening walk…" was rated
  `medium`. This is the measured Day 29 behaviour (dev precision 0.543, 9
  benign red-team controls escalate); the retrain is the first v2 item.
- The safety-classifier artifact logs sklearn `InconsistentVersionWarning`
  (trained on 1.5.2, running 1.9.1) — pre-existing, the same retrain item.

## Fix made during verification (root cause, not a weakened test)

**`cp .env.example .env` made a fresh clone unbootable.** The example file
ships every key blank; pydantic-settings parsed blank as an empty *string*,
so typed fields (`RAG_RETRIEVAL_LIMIT=`) failed validation and even
`alembic upgrade head` died. Fix: `env_ignore_empty=True` in
`Settings.model_config` — blank means "unset, use the default", which is
what `.env.example` documents. Regression tests added:
`test_a_fresh_clone_env_from_the_example_file_boots` (parses the real
`.env.example`) and `test_a_blank_env_value_means_unset_not_empty_string`.
One existing test was *extended*, not weakened: an explicit empty
`emotion_model_id` is still accepted for lexicon-only setups; a blank env
var now yields the default. Full backend suite re-run after the fix: 4160
passed (the +2 are the new regression tests).

## What a human must do before real users rely on this

1. **Clinical review** by a licensed professional (`docs/clinical-review.md`
   — no sign-offs exist).
2. **Phone-verify every helpline**, first the four flagged
   `needs_verification` entries; update `last_verified` only after a real
   check.
3. **Native-speaker review** of all Hindi/Bengali strings and non-English
   crisis rules (`translations/STATUS.md`).
4. **Legal and privacy review for the operating jurisdiction** (India DPDP
   Act; GDPR if EU users are possible; an age policy — none exists).
5. **Docker quick start + smoke on a Docker machine**; a real deployment per
   `docs/deploy.md`; the demo recording.
6. **Real-provider jailbreak run** with an API key
   (`evals/redteam/jailbreak_live.py --real`) — the sandbox only proved the
   fake-provider worst case.
7. **GitHub settings**: description, topics, Discussions, pin, social
   preview upload; then merge the PR, push the `v1.0.0` tag and let the
   release workflow publish.

## Honest overall assessment

The release tree is in the best state it has been: every local gate passes
(lint, 4160+601 tests with the branch gate, all eval thresholds, 150/150
red team at its last run), the fresh-clone path was genuinely broken and is
now genuinely fixed with regression tests, and the product finally says in
its own footer what it is — a beta portfolio demo. What this verification
does *not* establish: Docker-stack behaviour (no Docker here), real-browser
UX (CI covers it, this sandbox could not), real-LLM behaviour, and anything
clinical — the detection numbers are against synthetic data, over-escalation
on benign text is real and reproduced today, and the helplines still need a
human with a phone. v1.0.0 is an honest portfolio release, not a service.
