# Day 25 — PWA, accessibility and low-bandwidth experience: verification

Date: **2026-10-12**. Work is on `arena/23bea718-manovia`, the session-pinned
branch. The requested `day-25-pwa-accessibility-low-bandwidth` branch could not
be created in this session. Nothing was pushed to `main`, and **no PR was
merged**.

This is the record of Day 25's *verification* pass (Message 2). Message 1 built
the feature; everything below was run afterwards against the built app, and the
numbers are the ones the commands printed. Where a check failed, the failure,
its root cause and the fix are recorded (§8) instead of being quietly re-run.
Every fix shipped with a regression test that was shown to fail without it.

## Environment and scope

- Python 3.11.2 (backend `.venv`), Node 22.22.3, `npm ci` from the committed
  lockfile.
- Real FastAPI on `0.0.0.0:8000` against a throwaway SQLite database.
  - `LLM_PROVIDER=fake`, `EMOTION_ANALYZER=keyword`.
  - **Rules-only safety (`SAFETY_ML_ENABLED=false`)**, the documented test
    environment since Day 8 (see §8.6 for the run that forgot it).
  - Rate limits were raised for this server only, because the serial suites
    share one client IP: `RATE_LIMIT_AUTH_PER_MINUTE=120`,
    `RATE_LIMIT_GLOBAL_PER_MINUTE=6000`, `CHAT_RATE_LIMIT_PER_MINUTE=120`. The
    shipped defaults are unchanged.
- The **production build** (`npm run build`) was served by `vite preview` on
  `0.0.0.0:4173`, with the same-origin `/api` proxy (`changeOrigin: false`,
  matching nginx's `Host`). This is also the live preview.
  - The strict production CSP was checked separately, on `:4174`, by
    `scripts/verify-prod-csp.mjs`.
- Browser: real Chromium **153.0.8010.0** from npm's `@sparticuz/chromium`
  (`cdn.playwright.dev` is not reachable from this sandbox). Web security was
  **not** disabled.
- Tools:
  - Playwright 1.64.0.
  - axe-core 4.12.1, via `@axe-core/playwright` 4.13.0.
  - **Lighthouse 13.5.0**: mobile form factor, simulated throttling (150 ms
    RTT, 4× CPU slowdown).
  - vite 7.3.7, vite-plugin-pwa 2.0.0 / Workbox 7.4.1.
- Not available here:
  - Docker and nginx.
  - Real phones and screen readers.
  - Google Fonts. English makes no third-party requests anyway; Hindi and
    Bengali fall back to system fonts here.
- Verification added **no features**. It found:
  - **four product defects**;
  - **three defects in the test tooling**;
  - **one stale backend schema test**;
  - **one wrong figure in the ADR**;
  - one environment mistake of my own.

  All of them are listed in §8.

## PASS / FAIL table

All rows are from the **final build** (`index-Bk85qCtu.js`), after every fix in §8.

| # | Check | Result | Actual evidence |
| --- | --- | --- | --- |
| 1 | Lighthouse CI, mobile, on `/`, `/chat`, `/mood`, `/exercises`; four scores each | **PASS** | `LH_RUNS=3 npm run lighthouse` → exit 0, "All Lighthouse budgets met". **`/` 99 / 100 / 100 / 100 · `/chat` 98 / 100 / 100 / 100 · `/mood` 98 / 100 / 100 / 100 · `/exercises` 98 / 100 / 100 / 100** (performance / accessibility / best practices / SEO; median of 3 runs). CLS ≤ 0.015, TBT ≤ 20 ms, **0 third-party requests**. The pages were signed in: the audited loads called authenticated endpoints, which returned **200** (§1) |
| 2 | "PWA pass" | **PASS** | Lighthouse removed its PWA category in v12, so installability is checked in `e2e/day25/pwa.spec.ts` → **4/4 passed**. Chromium's own manifest parser (`Page.getAppManifest`) reports **0 errors**. Checked: name, `standalone`, start URL inside scope, 192 and 512 px PNG icons (real pixel sizes read from the files), a maskable icon, 180 px Apple icon, and the "Need help now?" shortcut `/?help=1`. The start URL is answered **by the service worker** with the network off. Negative control: a broken manifest *is* reported by the same parser |
| 3 | axe across all routes; list of serious/critical findings (target zero) | **PASS** | **110 scans** (55 per viewport): 11 routes × light/dark × standard/high contrast, 5 onboarding steps, the statement before onboarding, and the help dialog, exercise runner (2 states), mood confirmation and offline-queued states. **0 violations of any impact**, so serious/critical: **none**, moderate/minor: none. **0** interactive targets under 44×44 px |
| 4 | Playwright offline: help-modal helplines and breathing work offline; an offline mood check-in syncs after reconnect | **PASS** | `offline.spec.ts` **3/3 at both viewports**. Helplines served from cache, proven by an identical `X-Request-ID`. Breathing works after a full offline navigation (the timer advances, pause works). The queued check-in is stored server-side **exactly once** after reconnect, dated at creation. Plus the journal draft and the never-cached page (§3) |
| 5 | Keyboard-only walkthrough: onboarding → chat → mood → exercise → settings; focus traps and unreachable controls | **PASS** (one labelling defect found and fixed, §8.3) | `keyboard.spec.ts` 2/2, with **no mouse at all**. 7 surfaces × 2 viewports: **every walk wraps (no traps)** and **0 unreachable controls**. The help dialog's intended trap releases on Escape and returns focus to its button. Found: the chat page's two "Language" selects were indistinguishable to a screen reader |
| 6 | Zoom at 200% and 400% viewport; layout issues | **PASS after a fix** | First run **FAILED**: at 200% the page opened scrolled down to the bottom nav, and the first Tab landed on an off-screen link (§8.2). Final: **0 findings** at zoom 200% (640×400 @2x), zoom 400% (320×200 @4x) and text 200% (32 px root) on **11 routes**, and **0 px** horizontal overflow. Two new regression tests pass |
| 7 | Bundle sizes and the slowest route | **PASS** | `npm run budget` → "All bundle budgets met". Initial JS **115.1 KB gzip / 359.7 KB raw** (budget 130 KB), down from **173.0 KB / 623.3 KB** in one chunk at `dc9c749`. Every other route adds 1.2–7.2 KB (budget 25). **Slowest route: `/insights`**, 236.8 KB on a first visit, ≈ 1.63 s on slow 4G (the charts chunk is 107.8 KB). Precache 746.5 KB (budget 800) |
| 8 | Production CSP against the built bundle and the service worker | **PASS** | `PORT=4174 node scripts/verify-prod-csp.mjs` → exit 0: 7 routes HTTP 200, **0 blocked / 0 console errors**, service worker `ready` and **controlling** under `script-src 'self'` |
| 9 | Day 12–23 browser suite on the dev server (proves the `changeOrigin: false` proxy change) | **PASS after fixes** | First run: 43 passed / 9 failed. 8 failures were my environment, ML safety left on (§8.6); 1 was a real, pre-existing header overflow (§8.4). Final: **52 passed / 0 failed** (3.9 m) |
| 10 | Offline copy says what the app does (no background sync) | **FAIL → fixed → PASS** | The copy promised check-ins would send by themselves "when you're back online". They are sent only once the app is open again (§8.1) |

**Wrap-up checks (Message 3), run on the same final tree:**

| Check | Result | Actual evidence |
| --- | --- | --- |
| Backend lint and types | **PASS** | `ruff check .` "All checks passed!"; `ruff format --check .` "320 files already formatted"; `mypy app tests` "Success: no issues found in 266 source files" |
| Backend tests (full) | **PASS after a fix** | First full run: **1 failed**, 3676 passed, 3 skipped. The schema pin in `tests/unit/test_models.py` did not list Day 25's `mood_entries.client_id` (§8.7). After the fix: **3677 passed, 3 skipped** (5 m 13 s), coverage **95%** (11,328 statements, 519 missed) |
| Frontend lint and types | **PASS** | `npm run lint` (ESLint) exit 0; `npm run typecheck` (app + service worker) exit 0 |
| Frontend tests (full) | **PASS** | `npx vitest run`: **600 passed, 3 skipped**, in 73 files plus 1 skipped. The skips are live-backend tests |
| Production build | **PASS** | `npm run build` exit 0, with the same entry hash (`index-Bk85qCtu.js`) as the build verified above; precache 25 entries (746.49 KiB) |

## 1. Lighthouse, mobile (final build)

`LH_RUNS=3 npm run lighthouse`. The runner seeds a guest session, audits every
route three times and keeps the median.

| Route | Perf | A11y | Best pr. | SEO | FCP | LCP | TBT | CLS | Requests | Transfer | Authenticated API calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/` | **99** | **100** | **100** | **100** | 1.8 s | 1.9 s | 20 ms | 0 | 8 | 131.6 KB | `plan/today` 200 |
| `/chat` | **98** | **100** | **100** | **100** | 1.8 s | 2.1 s | 10 ms | 0 | 9 | 139.4 KB | none on load |
| `/mood` | **98** | **100** | **100** | **100** | 1.8 s | 2.1 s | 20 ms | 0.015 | 12 | 139.2 KB | `mood` 200, `mood/streak` 200 |
| `/exercises` | **98** | **100** | **100** | **100** | 1.9 s | 2.1 s | 10 ms | 0 | 11 | 161.0 KB | `exercises` 200, `exercises/history` 200 |

**Budgets** (`lighthouserc.json`):

- Errors:
  - performance ≥ 0.90.
  - accessibility ≥ 0.95.
  - CLS ≤ 0.1.
  - script ≤ 180 KB.
  - **0 third-party requests**.
- Warnings:
  - best practices and SEO ≥ 0.90.
  - LCP ≤ 2.5 s, TBT ≤ 200 ms.
  - total transfer ≤ 300 KB.

All were met. The same command run earlier in the pass, before the §8.2 and
§8.4 fixes, gave 99 / 98 / 98 / 98 for performance, so the scores are stable.

**For comparison, during the build** (not the baseline):

- Mobile performance was 85–87, with CLS 0.23 and SEO 91 (no `robots.txt`).
- The layout-shift fixes and `robots.txt` (ADR 0025) produced the numbers above.

**Caveats:**

- Simulated throttling in a shared sandbox is noisy, so expect ±2 points on
  real hardware.
- Lighthouse 13's new `agentic-browsing` category is not run.
- Lighthouse 13 needs Node ≥ 22.19. CI uses Node 22.

## 2. axe (final build)

`npm run test:e2e:prod`, `a11y.spec.ts`. Tags: WCAG 2.0, 2.1 and 2.2 A/AA,
plus best practices. The gate is zero serious/critical; everything is logged to
`test-results/day25/axe-*.jsonl`.

| Viewport | Scans | Serious | Critical | Moderate | Minor | Targets < 44 px |
| --- | --- | --- | --- | --- | --- | --- |
| 360×640 | 55 | 0 | 0 | 0 | 0 | 0 |
| 1280×800 | 55 | 0 | 0 | 0 | 0 | 0 |

**Surfaces (22):**

- Routes: home, chat, mood, journal, CBT, exercises, assessments, insights,
  settings, accessibility, not-found.
- Before sign-in: onboarding steps 1–5 and the accessibility statement.
- States: the help dialog, the exercise runner before start and running, the
  mood confirmation, and the queued-offline state with its banner.
- Each route is scanned in four appearances: light and dark, at standard and
  more contrast.

Also checked: nothing (header, bottom nav) covers the help dialog.

**Found and fixed during the build** (Message 1; listed so the zero is not
mistaken for "never had any"):

- Insights `aria-hidden-focus` and `scrollable-region-focusable` (serious).
- CBT and assessments contrast in dark mode (serious).
- 36–43 px bottom-nav targets.

## 3. Offline (final build)

`offline.spec.ts` ran at 360×640 and 1280×800, **6/6**. The network is cut with
`context.setOffline(true)`.

1. **Help from the cache, proven.**
   - The helpline response read offline carries the **same `X-Request-ID`** as
     the one cached online. A network answer would carry a fresh id.
   - "Need help now?" opens with real `tel:` links and a note that this is the
     saved copy.
   - The offline banner shows, and a service worker controls the page.
2. **Breathing.**
   - A full offline navigation to `/exercises`, then box breathing.
   - The countdown advances, and "Pause" works.
3. **Mood check-in.**
   - Offline, the form says it will keep the check-in on the device.
   - Saving focuses "Saved on this device", and **1** item sits in the outbox
     ("1 check-in is waiting").
   - After reconnect, the pending list empties and the live region announces
     "has been sent".
   - The server then holds **exactly one** matching row (valence 4).
     `recorded_at` falls between creation − 2 s and the reconnect time, so it is
     dated when made, not when sent.
   - The history shows the note.
4. **Journal draft.**
   - Written offline, it survives an offline reload: "restore" brings back the
     exact text.
   - On reconnect it saves, and the server's list contains it.
5. **A never-cached page** (Insights) says plainly that it needs a connection,
   and "Need help now?" stays one tap away.

Sending happens **when Manovia is next open and online**. There is deliberately
no background sync; see §8.1 and ADR 0025.

## 4. Keyboard-only walkthrough (final build)

`keyboard.spec.ts` uses only `keyboard.press` and `keyboard.type`. On each
surface, Tab is pressed until focus wraps back to the first stop.

| Surface | Tab presses to wrap | Distinct stops | Trap? | Unreachable |
| --- | --- | --- | --- | --- |
| Onboarding step 1 | 4 | 3 | no | 0 |
| Onboarding step 5 | 6 | 5 | no | 0 |
| Home | 24 | 23 | no | 0 |
| Chat (after a reply) | 23 | 22 | no | 0 |
| Mood (confirmation) | 23 | 19 | no | 0 |
| Exercise runner (paused) | 21 | 20 | no | 0 |
| Settings (high contrast on) | 45 | 44 | no | 0 |

The figures are identical at 360×640 and 1280×800.

- "Distinct stops" counts the browser's own UI (`body`) once.
- Mood has more presses than stops because Chromium tabs through the hour,
  minute and AM/PM fields of the reminder `<input type="time">`. That is native
  behaviour, not a trap.
- The walkthrough completes onboarding, sends a chat message, saves a check-in,
  and starts and pauses an exercise. It also switches on high contrast in
  Settings and opens and closes the help dialog. Escape releases the dialog
  and returns focus to "Need help now?".

**Finding** (fixed, §8.3): on the chat page, the header's interface-language
select and the chat panel's safety-copy language select were both announced as
"Language", with nothing to tell them apart.

## 5. Zoom and text resize (final build)

`zoom.spec.ts`. Browser zoom on a 1280×800 window is a smaller CSS viewport at a
higher pixel ratio.

| Mode | CSS viewport | Routes | Horizontal overflow | Clipped text | Sticky chrome at ≤ 512 px | Focus obscured |
| --- | --- | --- | --- | --- | --- | --- |
| Zoom 200% | 640×400 @2x | 11 | 0 | 0 | 0 | 0 |
| Zoom 400% | 320×200 @4x | 11 | 0 | 0 | 0 | 0 |
| Text 200% | 1280×800, 32 px root | 11 | 0 | 0 | n/a (800 px tall) | 0 |

**Regression tests added by this pass:**

- **Short screens:** at 200% and 400%, `/`, `/exercises` and `/settings` open at
  the top (`scrollY` 0). The first Tab lands on "Skip to main content", and the
  current page's item is visible in the bar (§8.2).
- **Header:** at 360 and 320 px, in English, Hindi and Bengali:
  - the page and the header row fit;
  - "Need help now?" is an on-screen target of at least 44×44 px;
  - in pseudo mode, the language select stays within 7rem (§8.4).

**First run (failed):**

- Zoom 200%, home: `a "Chat" under fieldset.border-0.p-0`, reproduced 3/3.
- The page had opened at `scrollY` 1067 of 1164, at the bottom navigation, and
  the first Tab went to the bar's "Chat" link, off screen. See §8.2.

## 6. Bundle sizes (final build)

`npm run budget` (gzip level 9 / raw, from Vite's manifest). The baseline is
`dc9c749`, built in a temporary worktree and measured the same way, from
whatever its `index.html` loads up front.

| | `dc9c749` (before Day 25) | Day 25 | Budget |
| --- | --- | --- | --- |
| Initial JS | **173.0 KB** / 623.3 KB, 1 file | **115.1 KB** / 359.7 KB, 3 files | 130 KB gzip |
| Entry chunk | 173.0 KB (every page but Insights, plus the hi and bn catalogues) | 39.2 KB | — |
| Initial CSS | 5.7 KB | 6.1 KB | 10 KB |
| Render-blocking third-party | Google Fonts CSS (2 Indic families) | **none** (fonts load only for hi/bn) | 0 |
| Precache after first visit | n/a (no service worker) | 746.5 KB raw, 25 files | 800 KB |

| Route | Adds (gzip) | First visit (gzip) | Est. on slow 4G |
| --- | --- | --- | --- |
| `/` | 0.0 KB | 121.2 KB | 0.91 s |
| `/chat` | 7.2 KB | 128.5 KB | 1.09 s |
| `/mood` | 5.4 KB | 126.6 KB | 1.08 s |
| `/journal` | 6.1 KB | 127.3 KB | 1.09 s |
| `/cbt` | 2.1 KB | 123.4 KB | 1.07 s |
| `/assessments` | 5.8 KB | 127.0 KB | 1.09 s |
| `/exercises` | 5.6 KB | 126.8 KB | 1.08 s |
| **`/insights`** | **115.6 KB** (budget 125) | **236.8 KB** | **1.63 s**, the slowest route |
| `/settings` | 6.5 KB | 127.7 KB | 1.09 s |
| `/accessibility` | 1.2 KB | 122.5 KB | 1.06 s |

**Largest chunks** (gzip / raw):

| Chunk | gzip | raw |
| --- | --- | --- |
| charts | 107.8 KB | 369.5 KB |
| react | 52.4 KB | 160.0 KB |
| index | 39.2 KB | 122.3 KB |
| framework | 23.5 KB | 77.4 KB |
| hi | 22.6 KB | 101.2 KB |
| bn | 21.9 KB | 103.1 KB |
| InsightsPage | 7.6 KB | 25.3 KB |
| ChatPage | 6.6 KB | 21.1 KB |

`/insights` is slow because of the chart library, which loads only there.
Lighter charts are a Day 26 candidate.

## 7. Production CSP

`PORT=4174 node scripts/verify-prod-csp.mjs` serves `dist/` with the headers
read from `docker/security-headers.conf`.

- Policy under test: `script-src 'self'`, no inline script, no eval.
- Walked `/`, `/chat`, `/journal`, `/mood`, `/exercises`, `/settings` and
  `/accessibility`: all **HTTP 200**, with **0** blocked resources and **0**
  console errors.
- Service worker: `{"supported":true,"ready":true,"controlled":true}`.
- nginx itself was not run (no Docker).

## 8. Defects found during verification, and what fixed them

### 8.1 Product: the offline copy promised background sync (`613c45c`)

**Symptom.** The copy promised more than the app does:

- `mood.offlineHint`, `mood.queuedBody`, `mood.pendingOffline`,
  `pwa.offlineBody` and `home.quickQueued` said a queued check-in would be sent
  "when you're back online — you don't need to do anything".
- The service worker deliberately has **no** background sync (ADR 0025). The
  outbox flushes only while Manovia is open and online.
- A person who closed the app on the train would believe it had been sent.

**Fix.** All five strings now say "…once you're back online **with Manovia
open**", in en, hi and bn. The threat model and the translations ledger were
updated to match.

**Regression.** `src/pwa/offlineCopy.test.ts` checks every string and confirms
the worker registers no `sync` handler.

- With the old locales it **fails 16/17**. The 17th case, "no sync handler in
  `sw.ts`", passes either way.
- With the new copy it passes 17/17.

### 8.2 Product: on short screens, pages opened at the bottom navigation (`7829410`)

**Symptom** at zoom 200%, 640×400:

- Every freshly opened page started at `scrollY` 1067 of 1164, looking at the
  bottom navigation.
- The first Tab went to the bar's "Chat" link, off screen, not to "Skip to main
  content".
- This hits anyone with a short viewport: 200% or 400% zoom, or a phone held
  sideways.

**Root cause.**

- Day 25 made the bottom bar scroll sideways, to keep 44 px targets.
- To keep the current page visible, it called `scrollIntoView()`, which scrolls
  *every* scrollable ancestor, including the page.
- That is harmless while the bar is fixed. But at heights ≤ 32rem the bar is
  deliberately static, so it scrolls away with the page instead of covering the
  focused control. There, `scrollIntoView()` dragged the document down to it.

**Fix.** `revealInStrip()` now scrolls only the bar, sideways.

**Test-tooling fixes in the same commit:**

- The zoom and keyboard specs reset with an *instant* `scrollTo`. Under
  `scroll-behavior: smooth`, the old `scrollTo(0, 0)` animated and raced the
  next Tab's focus scrolling.
- An off-screen focused control is now reported as "outside the viewport"
  rather than naming whatever sat at the clamped probe point.

**Regression.**

- `BottomNav.test.tsx` **5/5 fail** on the old component.
- The browser test "on a short screen a page opens at its top…" **fails** on
  the old build ("opened scrolled down: expected 0, received 1067") and passes
  on the new one.

### 8.3 Product: two identical "Language" controls on the chat page (`b95aa97`)

**Symptom.**

- The keyboard walkthrough reached two selects named "Language" on the chat
  page.
- One is the header's (the interface language). The other is the chat panel's,
  which only changes the pre-written crisis and check-in copy.
- The sentence explaining the second one sat below it, unconnected. A screen
  reader announced two identical controls (WCAG 2.4.6). This dates from Days
  12 and 23.

**Fix.** That sentence is now the select's accessible description
(`aria-describedby`).

**Regression.** `ChatControls.a11y.test.tsx` fails on the old component ("no
accessible description") and passes on the new one.

### 8.4 Product, pre-existing: an unbounded language select could push the header off a phone (`9215249`)

**Symptom.**

- The Day 18 exercises spec failed on the dev server at 360 px: the page
  scrolled sideways (`scrollWidth` 433).
- The culprit was the header's language and help group, ending at x = 433.

**Root cause.**

- A `<select>` is as wide as its longest option. The dev and QA option
  "Pseudo-localised (test mode)" grew the select to about 370 px.
- That pushed the header 73 px off screen and squeezed "Need help now?" to
  69 px. Production shows 133 px.

**Pre-existing since Day 23.**

- `dc9c749` overflows identically on its dev server. It was measured with only
  its `changeOrigin` patched in a throwaway worktree, because that build's
  proxy is otherwise rejected by the Day 22 origin guard.
- Days 23–24 had no browser, so nobody saw it.

**Who it affects.**

- Production readers never get that option.
- Measured on the built app, English, Hindi and Bengali fit **every route at
  360 and 320 px**, with no horizontal overflow.

**Fix.** The select is bounded at 7rem, which fits "English"; the native
control truncates anything longer.

**Remaining gap.** In pseudo mode the header still overflows by 29 px at 360
px, because pseudo padding expands even the brand name (83 px → 144 px). Many
labels elsewhere overflow too. Recorded as a pseudo-mode gap for Day 26.

**Regression.** The new header test fails without the fix ("language select
within 7rem: false"). The Day 18 spec passes again on the dev server.

### 8.5 Test tooling

**Report files mixed runs (`aa9e2bb`).**

- The axe spec appends JSON lines to `test-results/day25/`, but Playwright
  cleans only its `outputDir` below it. A second local run therefore reported
  both runs' scans, while `a11y.spec.ts` claimed the folder started empty.
- A `globalSetup` now clears the report files once per invocation (not per
  worker, since workers restart after a failure). Its first live run printed
  "cleared 8 report file(s) from a previous run".
- Regression: `src/lib/day25Reports.test.ts`. Its wiring cases fail on the old
  config.

**The dev-server suite ran the Day 25 specs (`34af267`).**

- `playwright.config.ts` uses `testDir: "./e2e"`, which is recursive.
  `npm run test:e2e` listed **94** tests, **42** of them Day 25 specs that need
  the production service worker.
- It now ignores `e2e/day25` and lists 52 tests in 7 files.
- Regression: in `day25Reports.test.ts`, failing on the old config.

### 8.6 Environment (mine, not the code): ML safety left on

**Symptom.** The first dev-suite run's API env omitted `SAFETY_ML_ENABLED=false`.

- The chat spec's ordinary message, "I had a bad day and I cannot switch my
  brain off", got the full crisis card instead of the fake model's reply.
- The classifier as loaded scores it **level 3 (high)**.
- That accounts for 8 of the 9 failures (chat ×6, CBT ×2). All of them
  passed once ML was off, as documented.

**Not a Day 25 regression.**

- This is the ML backstop's benign over-escalation, recorded as a known issue
  in PROGRESS.md since Day 11. Choosing the threshold is a safety-policy
  decision for the owner.
- The test environment has run rules-only ever since (`docs/assets/README.md`).
- Day 25 touched no safety code, model or dependency.
- The API also warns that the model was pickled with scikit-learn 1.5.2 and is
  loaded with the pinned 1.9.1. That is recorded as a known issue.

With the documented setting, the suite passed **52/52**. The earlier Day 25
keyboard runs that used that API exercised the **crisis card** by keyboard
instead of an ordinary reply. Its links were all reachable, which is useful
extra evidence; the final run uses an ordinary reply.

### 8.7 Backend schema pin not updated (`440e9f3`)

**Symptom.** The wrap-up's full backend run failed **1 of 3677**:
`tests/unit/test_models.py::test_mood_and_journal_columns_match_the_spec` found
an extra column, `client_id`.

**Cause.**

- Day 25 added the column on purpose (`5d3343e`): it is the idempotency key of
  a check-in queued offline (ADR 0025).
- After that change I ran the targeted suites (mood, migrations, OpenAPI,
  privacy) but **not the full suite**, which is where this pin lives.

**Fix.** The pin now lists `client_id`, with a comment saying why it exists.
This is not a weakened test: the pin's job is to make schema changes
deliberate, and this one is. Full suite afterwards: 3677 passed.

### 8.8 Documentation

**ADR 0025 quoted a wrong baseline (`e265095`).**

- It said "85 KB → 39 KB gzip", a figure from my notes made partway through
  the build.
- Building `dc9c749` gave **173 KB gzip (623 KB raw) in one chunk**. The ADR
  now quotes 173 → 115 KB, measured the same way on both builds.

**The Lighthouse runner's header comment** said Lighthouse 12; it is 13.

## 9. What this sandbox could not verify (manual checks)

1. Installing on a real phone: Android Chrome "Install app", and iOS Safari
   "Add to Home Screen". Then open the "Need help now?" shortcut from the icon.
2. Screen readers: NVDA + Firefox, VoiceOver on iOS and macOS, and TalkBack.
   Check the offline banner, the queued check-in announcement, and the
   description on the chat language select.
3. Windows High Contrast (`forced-colors`): focus rings and the breathing guide.
4. A real slow network: Chrome DevTools "Slow 4G" on a low-end Android device,
   especially `/insights`.
5. Docker and nginx: the `no-cache` header on `sw.js` and `index.html`, and the
   CSP from the real container.
6. A real CI run of `.github/workflows/a11y-lighthouse.yml`.
7. A native-speaker review of the Hindi and Bengali Day 25 strings. They are
   AI-drafted (🤖), including the §8.1 changes.
