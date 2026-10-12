# ADR 0025 — Offline-First Safety PWA, WCAG 2.2 AA High-Contrast Mode, and Low-Bandwidth Delivery

Date: 2026-10-12 · Status: accepted

## Context

A mental-wellbeing self-help companion is most needed in moments when connectivity, device hardware, or visual/motor conditions are least forgiving:

1. **Offline safety and self-regulation**: If a user opens Manovia on a weak mobile connection that drops out, clicking *"Need help now?"* or starting a breathing exercise must never fail with an empty error screen. At the same time, AGENTS.md safety rule 6 requires that helpline numbers live **only** in `backend/app/content/helplines.json` with a `last_verified` date — duplicating phone numbers into static frontend source code would create a second source of truth that could silently drift.
2. **Offline check-ins and unfinished thoughts**: Submitting a ten-second mood check-in or writing in the journal while offline should preserve the user's words locally on the device rather than discarding them or failing with a network error, while keeping personal content out of shared `CacheStorage`.
3. **WCAG 2.2 Level AA accessibility**: Beyond the existing 4.5:1 light/dark palettes, readers with low vision or contrast sensitivity need an explicit High-Contrast Mode (`>= 7:1` WCAG AAA text contrast and `3px` focus outlines), `>= 44x44` CSS pixel target sizes (WCAG 2.5.8 / 2.5.5), 200% text resize and 400% zoom reflow (`320` CSS pixels, WCAG 1.4.4 / 1.4.10) without horizontal scrolling, and an in-app Accessibility Statement page.
4. **Low-bandwidth performance**: Bundling `recharts` (`~370 kB`), all route views, and render-blocking third-party font stylesheets into the initial document slows First Contentful Paint and Largest Contentful Paint on budget Android phones.

## Decision

### 1. Original procedural PWA icons + Web App Manifest + Workbox `injectManifest` Service Worker

- **Original icons (`frontend/scripts/generate-pwa-icons.mjs`)**: A zero-dependency Node script renders Manovia's concentric-breathing-rings emblem analytically (3×3 supersampled signed-distance field, encoded directly to PNG) into `public/icons/pwa-192x192.png`, `pwa-512x512.png`, `pwa-512x512-maskable.png`, and `apple-touch-icon.png`.
- **Service Worker (`frontend/src/sw.ts`)**: Built via `vite-plugin-pwa` (`strategies: "injectManifest"`) and Workbox:
  - Precaches the app shell (`self.__WB_MANIFEST`) and binds `NavigationRoute` to `/index.html` for all non-`/api/` routes.
  - Caches `GET /api/v1/crisis/resources*` with `NetworkFirst` (`networkTimeoutSeconds: 3`, `manovia-crisis-resources-v1`) and warms it on `activate`.
  - Caches `GET /api/v1/exercises*` (excluding `/api/v1/exercises/history`) with `StaleWhileRevalidate` (`manovia-exercises-v1`) and warms it on `activate`.
  - **Privacy boundary**: Personal endpoints (`/api/v1/journal*`, `/api/v1/chat*`, `/api/v1/mood*`, `/api/v1/privacy*`, `/api/v1/auth*`, `/api/v1/exercises/history`) are **never** cached in Service Worker `CacheStorage`.
- **Client-side warm-up (`frontend/src/lib/pwa/offlineCache.ts`) + TanStack Query `networkMode: "always"`**:
  - On app mount while online, `warmOfflineResources` prefetches `/api/v1/crisis/resources` and `/api/v1/exercises` from the backend and stores the validated JSON payloads in `localStorage` (`manovia.offline.crisis.v1`, `manovia.offline.exercises.v1`).
  - `createQueryClient()` sets `networkMode: "always"` on queries and mutations so TanStack Query v5 never pauses offline queries/mutations before reaching the local cache or offline queue.

### 2. Offline mood check-in queue + local-only journal drafts + non-pushy install prompt

- **Offline mood queue (`frontend/src/features/mood/offlineQueue.ts`)**: Check-ins submitted while `navigator.onLine === false` (or when `fetch` fails with a network `TypeError`) are stored in `localStorage` (`manovia.mood.offlineQueue.v1`), shown immediately in `MoodConfirmation` (`data-testid="mood-queued-offline"`) and `MoodHistory` (`Waiting to sync` badge), and replayed in FIFO order under a module-level lock (`flushInFlight`) as soon as the `window` `online` event fires.
- **Local-only journal drafts (`frontend/src/features/journal/draftStorage.ts`)**: Unsaved edits in `JournalView` are persisted locally in `localStorage` (`manovia.journal.draft.v1`) with an explicit badge (`Local draft on this device (not uploaded)`) and a `Discard local draft` button, and are cleared immediately once encrypted on the backend.
- **Non-pushy install prompt (`frontend/src/lib/pwa/installPrompt.ts`, `InstallPromptBanner.tsx`)**: Captures `beforeinstallprompt`, suppresses the browser's intrusive mini-infobar, never opens a modal, and permanently honours a single tap on *"Not now"* (`manovia.pwa.installDismissed.v1`) while keeping an install button in Settings.

### 3. WCAG 2.2 AA accessibility pass + High-Contrast Mode + Accessibility Statement (`/accessibility`)

- **High-Contrast Mode (`[data-contrast="high"]` + `@media (prefers-contrast: more)`)**: Defined in `src/index.css`, `src/theme/ThemeProvider.tsx`, and `src/theme/first-paint.ts`, and asserted in `src/styles/tokens.test.ts` at `>= 7:1` (WCAG AAA) for every text/background pair in both light and dark themes, with `3px` focus outlines.
- **Remediation of real axe violations found by Playwright e2e**:
  - Replaced undefined `brand-*` and low-contrast `stone-500` classes in `JournalView.tsx` with the theme token classes (`bg-accent-bg`, `text-accent-fg`, `text-ink`, `text-ink-muted`).
  - Disabled Recharts' internal focusable SVG wrapper (`accessibilityLayer={false}`) inside `InsightPanel`'s `aria-hidden="true"` chart container and made `DataTableView`'s scrollable region keyboard-focusable (`tabIndex={0}`, `role="region"`).
  - Added `fieldset { min-width: 0; }` and `input, select, textarea { max-width: 100%; }` so 200% text resize and 400% zoom (`320` CSS px) never overflow horizontally.
- **Accessibility Statement (`frontend/src/pages/AccessibilityPage.tsx`)**: Reachable from the footer on every page and from Settings, with live interactive Contrast and Palette switches.

### 4. Low-bandwidth chunk splitting, non-blocking Indic fonts, and automated CI gate

- **Code splitting**: Heavy secondary routes (`InsightsPage` + `recharts`, `JournalPage`, `CBTPage`, `AssessmentsPage`, `AccessibilityPage`) are lazy-loaded via `React.lazy`, while `vite.config.ts` separates `vendor-charts`, `locales`, `vendor-react`, `vendor-i18n`, and `vendor-query`.
- **On-demand Indic web fonts**: `I18nProvider` loads Google Fonts (`Noto Sans Devanagari` / `Noto Sans Bengali`) asynchronously only when the active locale is `hi` or `bn` and online, eliminating render-blocking third-party requests on first paint.
- **Automated CI job (`.github/workflows/ci.yml` `axe-and-lighthouse`)**: Runs `e2e/day25.spec.ts` (axe WCAG 2.2 AA across all routes, offline resilience, keyboard-only walkthrough, 200%/400% zoom reflow) and `scripts/run-lighthouse-audit.mjs` (enforcing mobile `Performance >= 90`, `Accessibility >= 95`, `PWA pass`).

## Consequences

- **Positive**: Lighthouse mobile scores reach `95–100` Performance and `100` Accessibility across `/`, `/chat`, `/mood`, and `/exercises`; axe-core reports `0` violations across all 10 routes; helplines, breathing exercises, mood check-ins, and journal drafts work offline without violating the single-source-of-truth helpline rule.
- **Negative / Trade-offs**: Local journal drafts and queued offline mood check-ins reside in `localStorage` on the user's device until synced or discarded; shared-device users are reminded of this via the explicit badge and can discard drafts with one click or sign out.
