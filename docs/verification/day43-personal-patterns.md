# Day 43 — Personal patterns verification

Date: 2026-10-16. Branch: `arena/41b24aaf-manovia` (Arena-pinned; not main).
Decision: [ADR 0042](../adr/0042-local-personal-patterns.md).
Only synthetic, handwritten/generated data and fake source endpoints were used.

## Commands and real results

Run from repository root unless a `cd` is shown. Python dependencies installed
in `.venv`, frontend with `npm ci`. Logs kept outside Git under `/home/user`.

```sh
PATH="$PWD/.venv/bin:$PATH" make -C backend lint
PATH="$PWD/.venv/bin:$PATH" make -C backend test
cd frontend
npm run lint
npm run typecheck
npm run test:run
npm run test:run -- src/features/patterns
npm run build
npm run budget
npx playwright test e2e/patterns.spec.ts
```

Sandbox Chromium: Playwright's browser CDN is outside the permitted egress.
Installed **sandbox-only**, untracked `@sparticuz/chromium` from npm with
`--no-save --package-lock=false`; extracted its packaged libraries. Browser tests
ran with the following environment (not an app dependency or a production flag):

```sh
LD_LIBRARY_PATH=/home/user/browser-libs/lib \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/tmp/chromium \
PLAYWRIGHT_CHROMIUM_ARGS='["--no-sandbox","--disable-dev-shm-usage"]' \
npx playwright test e2e/patterns.spec.ts
```

Initial browser launch failed because `libnspr4.so` was absent. Extracting the npm
package's `al2023.tar.br` supplied the runtime libraries; no test was skipped or
weakened. The browser is Chromium, not real Firefox/Safari/mobile hardware.

## Recovered structure / false edges

120-day synthetic users: independent walk and sleep tags, 8 noise tags, bounded
continuous mood, known +1 walk and +0.7 sleep terms plus small noise.
`patterns.test.ts` prints estimates from the real browser-statistics implementation.

Seed 0, with all 10 comparisons corrected together:

| Recovered edge → mood | Mean difference | n matching / other | 95% bootstrap difference interval | Cohen's d | p |
|---|---:|---:|---|---:|---:|
| A walk | 1.08185 | 62 / 58 | [0.93946, 1.21166] | 2.77729 | 0.00049975 |
| 7+ hours sleep | 0.85450 | 48 / 72 | [0.67266, 1.04347] | 1.64220 | 0.00049975 |

Across **20 seeds**: **40/40** planted edges recovered; **0/160** false noise edges
(**0%**). Separate **20 completely null histories**: **2/200** false edges
(**1%** per candidate edge). These finite synthetic checks do not establish
real-world validity, independence, or causal attribution.

## Experiment simulations

Randomiser: exactly 7 distinct dates out of 14; Fisher-Yates with injectable RNG.
Over 1,000 seeded schedules, date-assignment counts were:
`498, 514, 486, 499, 507, 500, 514, 496, 488, 496, 500, 505, 500, 497`.
Production randomness uses WebCrypto. Analysis is by assignment and requires all
14 outcomes; partial experiments have no numeric result.

Actual true-difference analysis output:

> On assigned activity days, mood averaged 4.56; on other days, 2.14. 7 and 7
> days. Difference 2.41; 95% bootstrap interval 2.33 to 2.50. Two-sided
> permutation p=0.0010. A difference was observed in this fortnight. One small,
> unblinded experiment is uncertain; this is not a promise about future mood.

Actual constant/no-difference analysis output:

> On assigned activity days, mood averaged 3.00; on other days, 3.00. 7 and 7
> days. Difference 0.00; 95% bootstrap interval 0.00 to 0.00. Two-sided
> permutation p=1.0000. No clear difference was observed in this fortnight. One
> small, unblinded experiment is uncertain; this is not a promise about future mood.

**200 variable-outcome null simulations:** **7/200**, **3.5%** false positives
at nominal 5% alpha. Predeclared finite-simulation regression tolerance is 10%;
that tolerance is not substituted for the displayed 5% analysis threshold.

## Network inspection

Playwright instruments **every page request** and intercepts API calls. A unique
journal sentinel and derived payload markers must never appear in URLs/bodies.
The Private Mode flow imports journals, discovers/renames/merges/deletes themes,
selects graph/list nodes, randomises and records 14 outcomes, exports/resumes and
deletes: **0 API requests**, **no derived payloads**. Working data is absent from
local/session storage; reloading starts empty. Asset requests are not claimed to
be zero: opening the route downloads JavaScript/fonts as usual.

In standard mode, explicit existing-history loading made exactly these reads:

```text
GET /api/v1/mood?tz=UTC&limit=500                body=null
GET /api/v1/journal?limit=100&offset=0          body=null
GET /api/v1/exercises/history?limit=20&offset=0 body=null
```

No writes, no journal-derived query parameters, no graph/theme/experiment request.
Pagination has separate unit coverage; these endpoints are fixture-backed in
browser tests. Real account/network authentication remains a manual check.

## Accessibility / minimum samples / wording

- Browser tests: **6 passed**, the same three flows at 360×640 and 1280×800.
- Tab-only entry into graph, arrows, Home/End, Enter/Space; Tab into equivalent
  text-list selection and checkbox to hide the visual graph. No `.focus()`
  shortcut is used. Reduced motion is enabled; layout never animates in any mode.
- **axe: 0 violations** at graph, text-only, and completed-experiment states in
  both viewports. This does not replace a screen-reader usability review.
- Browser refusal test: four days yields no distribution table. Unit tests
  independently test each group below n=5. No point estimate/interval is shown.
- Wording guard scans app-owned graph/UI/experiment source plus generated
  pattern sentences: **0 causal/clinical-promise violations**; five deliberately
  bad claims are rejected. It does not censor a person's own journal or labels.
- Verification found a genuine keyboard-editing bug: clearing a theme name
  instantly reinserted its fallback. The new browser regression failed with
  `Expected: ""; Received: "Unnamed theme"`. Fixed at the source: allow empty
  editing, normalise on blur/export, and keep accessible node fallback labels.
  The same test passes with the fix. Tests were not weakened.
- Async deletion regression: resolving an in-flight source read after delete
  cannot resurrect the graph, theme/journal copy or experiment.

## Full regression results

First full run: backend **4,685 passed, 4 skipped, 50 warnings**; coverage gates
satisfied (overall statements **94.13%**, branches **86.11%**). Four skips are
optional NLP, ONNX and external-provider extras, not Day 43 checks. Backend ruff,
format (408 files), mypy (343 files) clean. Frontend **2,745 passed, 3 skipped**
before the added deletion regression. Final rerun results are recorded below in
the wrap-up section and PROGRESS.md.

Production build succeeds; all bundle budgets met. Explorer chunk **22.82 kB
raw / 8.56 kB gzip** (Vite's decimal kB). The new route is included in the bundle
budget inventory and production axe/zoom route sweep. No limit was raised.

## Manual checklist / known limits

1. With a real account, explicitly load a small history; inspect GETs/auth and
   compare daily mood, exercise titles and journal dates. Test travel/timezone
   changes (current source-date policy is documented in ADR 0042).
2. NVDA/VoiceOver plus keyboard: graph selection announcements, list alternative,
   topic editing, table headers, and experiment day labels; inspect dense 60-tag
   graphs and zoom. Browser automation is not a real assistive-technology test.
3. Export a plan, close/reopen, import it, record another day and re-export; verify
   private-file handling. Nothing is automatically saved. Delete does not remove
   source history, downloaded files or another tab's memory.

Day 43 copy is English-only. Lexical topics do not supply semantic embeddings.
First route load needs assets online (not install-time precached). Plaintext
exports contain personal derived data even though raw journals are excluded.
Confounding, missingness, selective tagging and serial correlation limit all
comparisons; the feature neither diagnoses nor forecasts.
