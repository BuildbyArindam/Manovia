# Day 18 — Guided exercises: verification

Date: **2026-10-11 (UTC)**. Work is on `arena/6cb19df8-manovia`, from the
supplied `963c3ba` base; nothing was pushed to `main`. Backend/content checkpoint:
`9373ff1`; frontend/browser checkpoint: `2e60b8f`. PR creation awaits the user's
explicit wrap-up instruction. **No PR or merge was performed.**

## Environment and scope

- Python 3.11.2, Node 22.22.3, npm 10.9.8; installed backend development extras
  and the frontend lockfile dependencies.
- Real FastAPI on `0.0.0.0:8000`, throwaway SQLite database migrated through
  `0005`, generated ephemeral development keys, fake LLM, keyword emotion
  analyser, offline models, safety ML and rate limiting disabled for the preview.
  Exercises do not call a model. No credentials are recorded here.
- Vite on `0.0.0.0:5173`, same-origin `/api` proxy and preview-compatible hosts.
- Real Chromium **153.0.8010.0**, obtained from npm's `@sparticuz/chromium@153.0.0`
  because the usual Playwright browser download host is unavailable in this
  sandbox. Bundled native libraries were extracted; browser web security was
  **not** disabled. Both viewport projects used Chromium, not physical devices.
- Verification added no product features. Browser-discovered root causes were
  fixed, regression coverage added, and all failed checks rerun.

## PASS / FAIL table

All reported commands were executed. PASS applies to the stated test scope,
not clinical effectiveness, all browsers, or production deployment.

| Check                                                     | Result                        | Actual evidence                                                                                                                                                                                                                                                                          |
| --------------------------------------------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Root `PATH="$PWD/.venv/bin:$PATH" make test`              | **PASS**                      | Exit 0. Backend **3023 passed / 3 skipped / 32 warnings**, **95% coverage**, 229.13s. Frontend **379 passed / 3 skipped**, 40 passed files / 1 skipped file, 53.54s.                                                                                                                     |
| Root `make lint` with the same PATH                       | **PASS**                      | Exit 0; Ruff clean, 272 files formatted, mypy 222 sources clean, frontend ESLint clean.                                                                                                                                                                                                  |
| Frontend typecheck, full Prettier check, production build | **PASS**                      | All exit 0; Vite compiled 754 modules in 6.49s.                                                                                                                                                                                                                                          |
| YAML schema and exact durations                           | **PASS**                      | `python scripts/validate_exercises.py`: all 8 files valid, every step sum matches. Runtime tests reject malformed/unsafe/duplicate-key/duplicate-ID/extra-field/mismatched-duration definitions.                                                                                         |
| Completion with ratings → real API history                | **PASS**                      | Fresh consented guest: POST **201**, pre **2**, post **4**, change **+2**; GET history **200**, exactly one matching row. Exact replay **200**, changed-payload replay **409**; history without auth **401**, without consent **403**.                                                   |
| Rule recommendations and visible explanation              | **PASS**                      | Backend rule tests plus browser high-anxiety choice → box breathing with “Why this?” and “You selected high anxiety”. No model or saved emotion selection.                                                                                                                               |
| Runner fake timers and lifecycle                          | **PASS**                      | **43 focused frontend tests**, including fractional pause/resume, exact final boundary, hidden/delayed-tab pause, stop without save, 3 interval-handle clears, 0 remaining timers, and finished-state stability for another 60 seconds.                                                  |
| Reduced-motion static guide in a real browser             | **PASS**                      | Both viewports: no animated SVG, visible static countdown and every one of the **26 real box-breathing YAML instructions**, frozen-clock pause/resume, keyboard-selected optional ratings, completion/history.                                                                           |
| Loaded-tab offline execution and explicit retry           | **PASS**                      | Both viewports: disconnected after loading; all steps run, failed save says unsaved; reconnect and identical UUID/payload retry leaves **one** API history row. No durable ratings queue or background sync.                                                                             |
| Real runner axe scans                                     | **PASS**                      | **8 scans**, ready/paused × light/dark × 360×640/1280×800; **0 violations in every scan**. Catalogue/runner also pass jsdom axe tests.                                                                                                                                                   |
| Breathing CSS retention and animation/pause               | **PASS**                      | Component-only Tailwind regression retains every phase without test-file safelisting. Built production CSS retains all 5 phase selectors, both keyframes and reduced-motion protection. Real browser computed `animationName=exercise-inhale`, then `animationPlayState=paused`.         |
| Exercise and interface copy review                        | **PASS (engineering review)** | Read all output from `validate_exercises.py --review`, all interface copy, rule reasons and rating summaries. No claim these brief exercises diagnose, treat, cure or guarantee relief; opt-outs and evidence limitations are explicit. **Not qualified clinical/translation sign-off.** |
| Physical vibration and actual screen-reader speech        | **NOT RUN — manual**          | Hardware/speech checks cannot be established in this sandbox. Default-off/blocked/unsupported haptic paths and step-only announcement DOM are covered by tests.                                                                                                                          |

Backend skips are two optional NLP-extra integration modules and one optional
Anthropic-extra test. Frontend skips are the three existing opt-in live-backend
chat tests, **not Day 18 exercise tests**. The full run still emits inherited
scikit-learn artifact-version warnings (artifact 1.5.2, runtime 1.9.1) and
React `act` warnings in existing mood tests; no warning was hidden or test deleted.
Those follow-ups remain outside Day 18.

Session-local raw evidence is in ignored `.cache/day18-root-test-final.log`,
`day18-root-lint-final.log`, `day18-frontend-targeted-final.log`,
`day18-e2e-initial.log`, `day18-e2e-rerun.log`, `day18-api-probe.json`,
`day18-copy-review.txt`, and `day18-production-css.log`. Generated Playwright
reports are ignored, not committed; this document preserves the actual results.

### Real-browser axe counts

| Viewport | Ready light | Ready dark | Paused light | Paused dark |
| -------- | ----------: | ---------: | -----------: | ----------: |
| 360×640  |           0 |          0 |            0 |           0 |
| 1280×800 |           0 |          0 |            0 |           0 |

The Day 18 suite ended **6 passed (1.3m), exit 0**, with no retries or API mocks.

## YAML validation output

```text
PASS body-scan                120s   7 steps  Short body scan
PASS box-breathing            120s  26 steps  Box breathing
PASS emotion-labelling         90s   4 steps  Name it to tame it: labelling
PASS grounding-54321          180s   7 steps  5-4-3-2-1 grounding
PASS muscle-relaxation        180s   6 steps  Short muscle relaxation
PASS slow-exhale-478          120s  14 steps  4-7-8 style slow exhale
PASS small-action-planner     120s   5 steps  One small action planner
PASS worry-time               120s   5 steps  Two-minute worry time
Validated 8 exercise YAML files; all duration sums match.
```

## Stored ratings observed over HTTP

This probe used a throwaway guest and synthetic ratings. The fetched history
row was identical to the POST response; an identical replay did not append a row.
No token or personal prose was printed.

```json
{
  "id": "7c1bbd44-5912-4eb8-8e74-d5ba67383fdf",
  "exercise_id": "box-breathing",
  "title": "Box breathing",
  "definition_sha256": "2f7ad92b0432f505a7dbc662ce42657739d1317471bd3abc7186e72f95681ff7",
  "duration_seconds": 120,
  "pre_rating": 2,
  "post_rating": 4,
  "change": 2,
  "summary": "You reported feeling 2 points higher afterwards (2 to 4 of 5). This is an observation, not proof that the exercise caused a change.",
  "created_at": "2026-10-11T18:11:16.369055Z"
}
```

A completion is self-reported, not server-verified adherence. Missing either
optional rating produces no comparison, not a fabricated zero. Tests also cover
lower/unchanged ratings, ownership isolation, simultaneous retries, and cascade.

## Failure → root fix → regression → rerun

The initial browser run was **5 failed / 1 passed**, not a success:

1. **Real defect:** dynamic `exercise-orb-${cue}` classes were purged by Tailwind,
   so the SVG's computed animation name was `none`. Replaced dynamic construction
   with a literal phase-class map. Added actual Tailwind-generated CSS coverage
   whose only scanned source is `BreathingGuide.tsx`; test selectors cannot
   accidentally safelist the production classes. Also checked production build
   output. The original computed-animation browser assertion remains intact.
2. **Navigation driver:** the desktop Exercises link's accessible name includes
   “Short practices”. Match the leading Exercises label, without changing the
   navigation component or excluding the desktop project.
3. **Rating driver:** clicking an `sr-only` native radio intercepted a different
   element. Use native focus + Space and assert it is checked; no forced click.
4. **Post-browser tooling:** generated Playwright reports contain minified vendor
   JS that the old ESLint/Prettier configuration tried to lint/format. Ignore
   generated artifacts only, with a regression proving actual runner/e2e/config
   source remains linted. Corrected three pre-existing formatting-only differences.

The full six-case browser rerun passed, and subsequent root lint/tests,
typecheck, formatting and build passed. No assertion was weakened or deleted.

## Copy review decisions

- Breathing: normal-sized breaths, comfortable shorter counts, optional holds,
  and normal breathing/stop instructions. Dizziness/breathlessness, unsuitable
  conditions, driving/water and unsafe settings are addressed. No sleep/anxiety
  treatment or physiology claims.
- Grounding: senses can be substituted; no requirement to touch unsafe objects,
  inhale substances, or eat/drink. Counts are not a test and unchanged feelings
  are not failure.
- Muscle relaxation/body scan: gentle effort only, injured areas skipped, no
  breath-holding or tooth clenching; eyes-open/outside-focus/stop alternatives.
  Not medical rehabilitation or diagnosis.
- Labelling: tentative/private labels, no forced recall, and an explicit reminder
  that “name it to tame it” is a nickname, not an outcome promise.
- Worry time: never postpones immediate safety/practical help; rumination opt-out;
  two minutes bounds attention, not problem-solving or relief.
- Small action: resources/energy/safety respected, rest/private choice allowed,
  no productivity/willpower test or commitment. A limited self-help idea from
  behavioural activation, not the established therapy itself.
- Recommendations/history: person-selected feelings, reasons not diagnoses;
  self-reported association, no efficacy score or causal improvement claim.
- Instructions are **English only**; hi/bn titles do not imply translated,
  clinically reviewed instructions. Qualified human review remains required.

## Re-run the verification

With dependencies installed, from the repository root:

```bash
PATH="$PWD/.venv/bin:$PATH" make lint
PATH="$PWD/.venv/bin:$PATH" make test
npm --prefix frontend run typecheck
npm --prefix frontend run format:check
npm --prefix frontend run build
.venv/bin/python scripts/validate_exercises.py
.venv/bin/python scripts/validate_exercises.py --review
npm --prefix frontend run test:run -- src/features/exercises --reporter=verbose
```

For browser checks, start a **migrated development/test API** with fake providers
and a same-origin Vite proxy, then run:

```bash
npm --prefix frontend run test:e2e -- exercises.spec.ts
```

The suite checks `/api/v1/ready` and fails if the API is missing; it does not skip.
Normal environments use Playwright's installed Chromium. This sandbox's executed
command also set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/tmp/chromium`,
`PLAYWRIGHT_CHROMIUM_ARGS` to
`["--no-sandbox","--disable-dev-shm-usage","--no-zygote","--use-gl=angle","--use-angle=swiftshader"]`,
and `LD_LIBRARY_PATH` to the npm runtime's bundled `/tmp/al2023/lib`. The local
runtime metadata is in ignored `.cache/day18-browser.json`; it is not a dependency
or committed browser binary.

## Remaining short manual checklist

1. **Screen readers:** NVDA/Firefox and VoiceOver/Safari. Verify entry/finish/return
   focus, each step announced once, no spoken tick spam, pause/resume and optional
   ratings operable with a keyboard.
2. **Physical phones:** default-off vibration, optional step cues and cancellation
   on pause/stop on a supported Android browser; visible text/static fallback on
   unsupported iOS/browser combinations. Check actual OS reduced-motion settings.
3. **Human content review:** qualified self-help/safety review of all eight
   definitions/evidence notes and fluent hi/bn title review before broader use.

Offline support is deliberately the **already-loaded tab only**. Closing/leaving
loses unsaved state; no offline reload/PWA, personal plaintext queue, automatic
sync, or promise of offline crisis resources was introduced. See
[ADR 0018](../adr/0018-guided-exercises.md) for the privacy/timing decisions.
