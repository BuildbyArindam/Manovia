# ADR 0025 — Offline help, accessibility to WCAG 2.2 AA, and a budget for low-end phones

Date: 2026-10-12 · Status: accepted

## Context

Manovia's users include people on ₹8,000 Android phones, on 3G/slow 4G, on
trains with no signal, and people who use a keyboard, a screen reader, zoom or
Windows high contrast. Until Day 24 the app needed a live connection for
everything — including the "Need help now?" dialog, whose helplines came from
`GET /api/v1/crisis/resources` on every open — and the first visit downloaded
85 KB (gzip) of main entry JavaScript, including the full Hindi and Bengali
catalogues, plus two Google Fonts families, before anything rendered.

Day 25 had to make four things true at once, and they pull against each other:

1. **The two things a person in distress may need most must work offline**:
   the helplines and a breathing exercise. A crisis does not wait for a signal.
2. **Offline support must not become a second, weaker data store.** Days 3–22
   built encryption at rest, consent gates and real deletion. A service worker
   that cached `/journal` responses would keep decrypted entries in Cache
   Storage after sign-out and after "delete my account".
3. **Work done offline must not be lost or duplicated.** "Send it when the
   connection returns" is exactly the situation in which a response is lost and
   a request retried.
4. **Accessibility and speed must be enforced, not described.** A WCAG claim
   nobody re-checks decays with the next feature.

## Decision

### 1. A hand-written service worker with a two-endpoint allowlist

`vite-plugin-pwa` with the **`injectManifest`** strategy: the worker is our own
code (`frontend/src/sw/sw.ts`, ~150 lines using Workbox modules), and the
privacy boundary is a separate, unit-tested module (`src/pwa/swRoutes.ts`,
asserted by `swRoutes.test.ts`).

- **Precached:** the app shell — `index.html`, JS/CSS chunks, icons, manifest
  (content-revisioned by the build). The Insights page and the chart library
  (`charts-*`, the largest chunk) are filtered out (`src/pwa/precache.ts`):
  Insights is a view of server data and useless offline.
- **Navigations** to client-side routes are answered with the cached shell, so
  `/exercises` typed into the address bar works offline.
- **Exactly two public, unauthenticated API answers are cached at runtime:**
  - `GET /api/v1/crisis/resources` — **network first, 3-second timeout**, then
    the saved copy (30-day expiry). Numbers can change, so the network wins when
    it answers; on a weak connection the person sees numbers within seconds.
    The data still comes only from `backend/app/content/helplines.json` via the
    API; nothing is hard-coded in the frontend (AGENTS.md safety rule).
  - `GET /api/v1/exercises` and `/exercises/{id}` — stale-while-revalidate,
    60-day expiry. `history` and `recommendations` are excluded (personal /
    emotion-dependent).
  - Both are **warmed at install**, so they work offline even for someone who
    never opened them online.
- **Never cached:** any request carrying `Authorization`, even if its path
  looks public, and every personal endpoint. Only status `200` is stored.
- **No background sync, no token in the worker.** The worker cannot send
  anything as anybody.
- **Updates wait for the reader.** The first install claims the page (offline
  works without a reload); later versions show a quiet "Reload to update"
  notice with no countdown — a timed reload is a time limit (WCAG 2.2.1) and an
  interruption in the middle of a breathing exercise.
- **Production build only.** The dev server never registers a worker, so a
  cached shell can never hide a code change while developing.

### 2. Offline mood check-ins: a page-level outbox with server-side idempotency

`frontend/src/features/mood/outbox.ts` queues a check-in in `localStorage`
(`manovia.mood.outbox.v1`, capped at 50) when the request fails for a
**network** reason (`isNetworkFailure`, not any error). Each item carries:

- a random **`client_id`** the server treats as an idempotency key —
  `POST /mood` stores one row per `(user_id, client_id)` (unique index
  `ux_mood_entries_user_id_client_id`, migration `0009`); a repeat answers
  `200` with the stored row and `replayed: true`; a concurrent duplicate that
  loses the insert race is resolved by the `IntegrityError` path;
- **`recorded_at`**, when the person actually checked in — accepted up to
  **7 days back**; up to 10 minutes in the future is clamped to now (a fast
  phone clock); anything further is `422 invalid_recorded_at`;
- the **`userId`** it was made under, so a check-in made under one account is
  never sent under another (`account_changed`).

`OutboxSync` (mounted once in the shell) sends the queue on `online`, on app
start / tab visible, and every 60 s while something waits. **A replay is still
safety-screened**, and if a synced note comes back with crisis help, the help
is shown — the only reason it was not shown at the time is that the phone had
no signal. A `503 safety_unavailable` is retried, never skipped: a note is
never stored unscreened. Items that can never be sent (too old, wrong account,
rejected) stay listed with a plain reason until the person discards them.

### 3. Journal: one local draft, offered back, never sent by itself

`src/features/journal/drafts.ts` keeps a single draft
(`manovia.journal.draft.v1`) while the editor holds words the server has not
confirmed. It is removed the moment a save succeeds, **offered back (never
silently applied)** on the next open, expires after 30 days, and is never
synced. The journal's encryption at rest happens on the server, as before.

### 4. Plain text on the device, stated rather than "encrypted"

The queued note and the draft are plain text in `localStorage` until sent,
saved or discarded — the same trust boundary as the session token already
there. Both are cleared on sign-out and when onboarding restarts
(`OnboardingProvider`). The copy says "kept on this device" and the threat
model (§6a) lists it as a residual risk.

### 5. A quiet install offer

`beforeinstallprompt` is captured and the browser's own mini-infobar
suppressed; the **only** place installation is offered is a section in
Settings. No modal, no home-screen banner, never on a first visit, and "Not
now" is remembered for 90 days. iPhone/iPad get the manual Share → Add to Home
Screen instructions. The manifest (`public/manifest.webmanifest`) has original
icons generated from the existing leaf mark (`npm run icons`), maskable
variants, and a **"Need help now?" shortcut** (`/?help=1` opens the dialog).

### 6. Accessibility: WCAG 2.2 AA as the floor, with three stricter choices

- **44 × 44 px minimum targets** (WCAG 2.5.5 AAA, not just 2.5.8's 24 px AA);
  helpline links ≥ 48 px. Enforced in CSS and measured in a browser.
- **A high-contrast mode** (Settings switch; follows `prefers-contrast: more`
  when unset; applied before first paint): ≥ 7:1 text in light and dark,
  asserted from `index.css` by `contrast.test.tsx`. `forced-colors` (Windows
  high contrast) is handled separately.
- **Nothing timed against the person**: exercises pause, updates wait, and a
  network failure during token refresh no longer signs anyone out (it is not
  an expired session).

Plus: root font size `max(100%, 16px)` so browser text size is respected;
reflow at 200 %/400 % zoom and 200 % text-only without horizontal scrolling;
landmarks and a skip link; labels and linked, announced errors; live regions
for replies, saves and connection changes; `prefers-reduced-motion` switches
animations off including the breathing guide; the dialog is portalled so the
bottom navigation can never cover it. An **accessibility statement**
(`/accessibility`) is reachable before onboarding and lists what does not work
yet.

### 7. Performance: send less, later

- **Route-level code splitting**: `React.lazy` for every page except Home,
  with `manualChunks` for `react`, `framework` and `charts`. `main.tsx` calls
  `preloadRoute(location.pathname)` before React renders, so a deep link's page
  chunk downloads in parallel with the entry chunk instead of one round trip
  later.
- **Hindi and Bengali catalogues load on demand** (~22.5 KB gzip each), and the
  Noto Devanagari/Bengali fonts are requested **only** when that language is
  chosen. English visits make **zero third-party requests**. The main entry
  chunk went from 85 KB to 39 KB gzip.
- Layout-shift fixes took mobile CLS from 0.23 to ≤ 0.015: the lazy-page
  fallback reserves a screen's height, and Home renders the data-free check-in
  above the data-driven plan card (which reserves its own height).

### 8. Enforcement: budgets that fail the build

- **`npm run budget`** (`scripts/check-bundle-budget.mjs`) follows the real
  import graph in `dist/.vite/manifest.json`: initial JS ≤ 130 KB gzip, CSS ≤
  10 KB, each route ≤ 25 KB (Insights ≤ 125 KB), precache ≤ 800 KB raw.
- **`npm run lighthouse`** (`scripts/lighthouse-ci.mjs`) runs Lighthouse 13 on
  the mobile profile against signed-in pages and asserts
  `lighthouserc.json`: performance ≥ 0.90, accessibility ≥ 0.95 (errors),
  CLS ≤ 0.1, script ≤ 180 KB, third-party requests = 0.
- **`npm run test:e2e:prod`** (Playwright against the production build):
  axe-core on every route in light/dark × standard/high contrast × mobile/
  desktop; keyboard walkthroughs; zoom reflow; offline; installability.
- **"PWA pass" is our own test**, because Lighthouse removed its PWA category
  in v12: `e2e/day25/pwa.spec.ts` reads the manifest the browser parsed
  (`Page.getAppManifest`), checks icons by their real PNG size, proves the
  start URL is served by the worker offline, and includes a negative control.
- **CI:** `.github/workflows/a11y-lighthouse.yml` runs all of it on every pull
  request and push to `main`, uploading the reports either way.

## Alternatives rejected

- **`generateSW` with a `runtimeCaching` regex list.** Less code, and the
  privacy boundary becomes a config pattern that one careless `/api/v1/.*`
  widens to "cache every decrypted journal entry". A named predicate with unit
  tests and an `Authorization` veto is harder to get wrong.
- **Workbox Background Sync for check-ins.** Chromium-only, and it needs the
  session token readable by the worker (IndexedDB), widening where the token
  lives. Worse, a background replay's answer is seen by nobody — and that
  answer may be the crisis helplines for a note that read as high risk.
- **Caching personal data for offline reading** (journal, mood history). The
  most-requested convenience and the one that breaks deletion: Cache Storage
  outlives sign-out and the server-side cascade.
- **Encrypting the outbox and draft on the device.** The key would live on the
  same device as the ciphertext, so it would add complexity and a false sense
  of protection, not protection.
- **Hard-coding the helplines in the bundle for offline use.** Violates the
  single-source rule (`helplines.json` only) and could never update.
- **`@lhci/cli`.** It pinned Lighthouse 12 and an old Puppeteer, adding 11 high
  severity advisories to `npm audit`. A ~280-line runner on Lighthouse 13 keeps
  the familiar `lighthouserc.json` assertion format.
- **A prominent "Install Manovia" banner.** Measurably better install rates;
  the wrong thing to show someone who opened a wellbeing app on a hard day.

## Consequences

- **Offline help is real but can be stale**: up to 30 days old, labelled as the
  saved copy, each entry with its `last_verified` date.
- **Check-ins wait until the app is next open and online** (no background sync)
  and their date depends on the device clock (> 10 min fast → `time_rejected`).
- **Plain-text residue on the device** (queued note, one draft) until sent,
  saved or discarded — a documented residual risk, cleared on sign-out.
- **Two caches to keep honest**: a contract change to `/crisis/resources` or
  `/exercises` must bump `CACHE_NAMES` (`-v1` → `-v2`).
- **Hindi and Bengali first loads cost one extra request** (catalogue + font).
  Their Day 25 strings are AI-drafted (🤖) pending native review.
- **The dev/preview proxies now preserve `Host`** (`changeOrigin: false`), as
  nginx does, so the Day 22 Origin guard sees the same thing everywhere.
- **Lighthouse needs Node ≥ 22.19** in CI (the app's engines stay ≥ 20.19).
  Lighthouse scores in the sandbox are simulated throttling and vary by a few
  points run to run; the budgets have headroom for that, not for regressions.
