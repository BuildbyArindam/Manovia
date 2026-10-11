# Day 21 — Daily plan and Week-3 review: verification

Date: **2026-10-12**. Work is on `arena/60ab9eba-manovia` (the session-pinned
branch; the requested `day-21-daily-plan-week-3` branch cannot be created in
this session). Nothing was pushed to `main`. **No PR was merged.**

## Environment and scope

- Python 3.11.2, Node 22.22.3; backend venv at the repo root `.venv`, frontend
  `node_modules` installed from the lockfile.
- Real FastAPI on `0.0.0.0:8000` against a throwaway SQLite database migrated
  fresh through `0007`, generated development keys, `LLM_PROVIDER=fake`,
  rules-only safety (`SAFETY_ML_ENABLED=false`). The serial e2e shares one
  client IP, so its rate limits were raised for the test server only
  (`RATE_LIMIT_AUTH_PER_MINUTE=300`, `RATE_LIMIT_GLOBAL_PER_MINUTE=600`;
  shipping defaults 10 and 120 unchanged) — without that, a fast full run
  legitimately trips the global 120/minute limit and flakes.
  Vite on `0.0.0.0:5173` with the same-origin `/api` proxy. No Docker: the
  sandbox has no docker binary, so the compose stack itself was not exercised —
  the API and dev server were started directly instead.
- Real Chromium **153.0.8010.0**, obtained from npm's `@sparticuz/chromium@153.0.0`
  (the same route Days 18 and 19 used) because `cdn.playwright.dev` is not
  reachable from this sandbox. The bundled AL2023 shared libraries were inflated
  alongside the binary; browser web security was **not** disabled.
- Verification added no product features. The defects the browser run found
  were fixed at the root cause and rerun; no assertion was weakened. They were:
  two stale e2e helpers from before the home screen existed, one ambiguous
  role locator, and one e2e step that assumed a chip cap the product has never
  had (latent since the caps split — jsdom tests pin the real behaviour).

## PASS / FAIL table

All reported commands were executed. PASS applies to the stated scope, not
clinical effectiveness, all browsers, or production deployment.

| Check | Result | Actual evidence |
| --- | --- | --- |
| Stay on the pinned branch; no new features during verification | PASS | all commits on `arena/60ab9eba-manovia`; verification changed only tests/docs |
| `make lint` | PASS | ruff check + format 296 files, mypy 244 files, eslint — all clean |
| `make test` backend | PASS | **3354 passed, 3 skipped, 0 failed** (3 optional extras not installed); coverage **95%** overall; Day-21 modules 96–100% (`services/plan/daily_plan.py` and `api/v1/plan.py` at 100%) |
| `make test` frontend | PASS | **438 passed, 3 skipped** (live-backend tests skip without an API), 49 test files |
| Planner vs eight synthetic profiles | PASS | `scripts/verify_day21.py`: all 8 printed, ≤3 items each, every suggestion explained, quiet-hours profile empty, item-9 profile support-only |
| Playwright e2e (all six specs, both viewports) | PASS | **42 passed, 0 failed** in real Chromium 153 at 360x640 and 1280x800 against the live API; first run caught two stale-helper regressions, fixed and rerun green |
| `evals/run_safety_eval.py --split dev` | PASS | ran clean: HIGH+IMMINENT precision 0.543, recall **1.000**, 0 false negatives; 31/51 benign escalated — identical to Days 9/14, confirming no safety regression (and the open false-positive debt) |
| Week-3 top-five risks/debts | PASS | `PROGRESS.md` section with quick wins fixed in the same pass |
| Migration on a fresh database | PASS | `alembic upgrade head` ran 0001→0007 on a brand-new SQLite file |
| Live API smoke of the new endpoints | PASS | guest+consent→201; `GET /plan/today?tz=Asia/Kolkata`→200 with one explained suggestion; settings PUT/GET round-trip incl. midnight-crossing quiet hours; done then skip both 200 |
| Tag `v0.3.0` | PASS | tagged at the verified head of this branch |

## Root causes found and fixed during verification

1. **Seed-script row counts** — `SEEDED_COUNTS` lacked the two Day-21 tables,
   which broke three seed tests (the seed script counts every table in the
   metadata). Fixed by declaring `plan_items: 0` and `user_settings: 0` under
   the same "the seed does not invent opt-in rows" convention as Days 18/19.
2. **Stale e2e helpers** — before Day 21 onboarding landed on `/chat`; the home
   screen moved the landing to `/`, so `chat.spec.ts` expected a heading that no
   longer appears and `mood.spec.ts` matched two "Mood" links. Both helpers now
   wait for the home screen to settle, then navigate to the page their spec
   exercises; every behavioural assertion in those specs is unchanged.
3. **Ambiguous locator** — `getByRole("radio", { name: "Good" })` in the mood
   keyboard spec substring-matched both the "Good" and "Very good" faces. It
   now matches exactly.
4. **Stale chip-cap assumption** — the mood keyboard spec selected two emotions
   plus the "Sleep" *factor* and expected the fourth emotion chip disabled,
   assuming one shared cap. The shipped behaviour (documented on `TagChips` and
   pinned by its jsdom tests) is three emotions and five factors, separately.
   The step now selects three emotions to reach the cap, and additionally proves
   a factor chip stays available at the emotions cap — a stronger test of the
   real behaviour.

## What this sandbox cannot check (human checklist)

- Docker compose stack (`make up` / `make smoke`) and PostgreSQL — no docker
  binary here; migration 0007 and the whole suite were verified on SQLite only.
- Screenshots in `docs/assets` — listed there as expected-but-generated-on-a-
  browser machine.
- The safety classifier's HIGH false positives on ordinary phrases (open since
  Day 19) need a human labelling pass before any threshold change.
