# 0005 — Frontend skeleton: tokens, the API client, and the accessibility floor

Date: 2026-10-09
Status: Accepted

## Context

Day 5 builds the first frontend: `frontend/` with Vite + React 18 + TypeScript
(strict) + Tailwind + React Router + TanStack Query + Vitest + Testing Library +
ESLint + Prettier. Three product constraints shape it more than any library
choice:

1. **Calm, warm, low-stimulation, not clinical blue.** The palette is a design
   requirement, not decoration.
2. **Accessibility is a rule, not a phase** (AGENTS.md): keyboard navigable,
   contrast ≥ 4.5:1, respects `prefers-reduced-motion`, focus management.
3. **Safety-first.** The crisis path must be reachable without an account, and
   onboarding must not be completable without the required consents.

## Decisions

### 1. Design tokens as CSS variables, one `data-theme` switch

Every colour, font and focus colour in the app is a `--manovia-*` custom
property in `src/index.css`, with two complete palettes: warm paper (light) and
warm charcoal (dark), swapped by a `data-theme` attribute on `<html>`. Tailwind
is configured to *reference* those variables (`bg-surface`, `text-ink`), so the
same markup renders in both themes and no component contains a literal colour.

The contrast numbers are enforced by `src/styles/tokens.test.ts`, which parses
the CSS and asserts ≥ 4.5:1 for every text/background pair in both themes and
≥ 3:1 for the focus ring and strong borders (WCAG 1.4.11, non-text contrast).
A colour change that breaks the ratio fails the suite. The same test pins the
16px base font size and the `prefers-reduced-motion` block, and guards the
design direction (the accent must stay green-dominant, i.e. sage, not the
clinical blue this palette exists to avoid).

`index.html` carries a six-line inline script that sets `data-theme` before the
first paint, so a dark-mode user never sees a white flash.

### 2. One API client, one error envelope, one refresh

`src/lib/api.ts` is the only code that talks to the backend. It:

- turns every failure into an `ApiError` carrying the Day 2 envelope
  (`{error: {code, message, request_id}}`), so the UI can show a curated message
  and quote a request id;
- refreshes on 401 **and** proactively when the access token is inside the
  refresh skew, with single-flight semantics: concurrent 401s share one
  `POST /auth/refresh` instead of stampeding it;
- replays a failed request exactly once, so a persistent 401 cannot loop;
- clears the session and calls `onAuthExpired` when the refresh itself fails.

The token store is injected (`MemoryTokenStore` for tests,
`BrowserTokenStore` for the app), which is what makes the refresh tests
possible without a browser. Endpoint shapes live in `src/lib/endpoints.ts`, so a
backend rename is a compile error.

### 3. Onboarding is a gate, not a route

`App` renders `OnboardingFlow` *instead of* the shell until the agreements are
recorded, so no surface (chat, journal, mood) is reachable without them. The
flow is welcome → what Manovia is and is not → AI disclosure → agreements →
guest or account. The consent gate is enforced twice: the button is disabled,
and `OnboardingProvider.complete()` re-checks the required consents itself, so a
UI refactor cannot quietly drop it. Each agreement is a separate checkbox and
every one starts unticked; the optional one (chat history) is never required.

The AI disclosure text and the consent versions come from
`GET /api/v1/consent/requirements` (the server is the source of truth), with a
fallback copy for when the API is unreachable. `src/onboarding/content.test.ts`
reads `backend/app/content/consent_documents.json` and fails if the fallback
drifts from it.

### 4. The modal owns its focus contract

`src/components/Modal.tsx` is the only dialog primitive: `role="dialog"`,
`aria-modal`, labelled and described by the caller, focus moved in on open and
returned to the opener on close, Tab/Shift+Tab cycling inside, Escape to close,
backdrop click to dismiss, page scroll locked. Every dialog in the app is built
on it, and `Modal.test.tsx` asserts the contract (Tab is dispatched directly,
because `user-event` implements its own tab order and would ignore the trap's
`preventDefault`).

### 5. One navigation, two presentations

`AppShell` asks `useMediaQuery("(min-width: 768px)")` and renders the side rail
or the bottom bar — not both with CSS hiding one. The rendered markup therefore
matches what the user can reach, and the behaviour is testable (a stubbed
`matchMedia` decides which navigation appears).

### 6. The crisis endpoint is public

`GET /api/v1/crisis/resources` serves `app/content/helplines.json` with no
authentication, no consent gate, and no rate-limit budget beyond the global one:
someone in trouble has not signed in and must never be asked to. The file is
validated on load (unique ids, at least one contact method per resource,
dialable characters only, a `last_verified` date) — a broken helpline file is a
loud configuration error, not a silently truncated list. The frontend's dialog
always states the emergency instruction from static copy, so a failed fetch is
never a dead end.

### 7. Tokens in web storage, with the trade-off written down

`BrowserTokenStore` keeps the refresh token in `localStorage` so a reload does
not sign the user out. That is XSS-readable, and the alternative (an httpOnly
refresh cookie) is a backend change with CSRF consequences. Recorded as a known
issue and a parking-lot item rather than pretended away.

## Consequences

- The palette can only change inside `src/index.css`; a stray literal colour in
  a component is a review finding, and the token test catches the ones that
  matter.
- Contrast is verified by a unit test, not by a browser audit: `jest-axe`
  disables colour-contrast rules in jsdom (it cannot compute styles there), so
  the ratio check has to be arithmetic.
- The frontend has no live browser test: this sandbox cannot download a
  Playwright browser (no CDN access), so the served app is checked with curl and
  the rendered component tree is checked in jsdom. A real browser pass over
  onboarding + the help modal is on the Day 6 checklist.
- `npm run test` watches by default (vitest's behaviour in a TTY); CI and this
  repo's verification use `npm run test -- --run`.
- The dev server proxies `/api` to the backend and binds `0.0.0.0`, so the
  preview works behind the sandbox's proxy host and the browser never calls
  `localhost` directly.

## Alternatives considered

- **CSS-in-JS or a design-token package**: rejected — CSS variables are
  framework-free, inspectable in devtools, and switchable without React.
- **Tailwind's `dark:` variants for theming**: rejected — it duplicates every
  colour decision across two class sets. Variables make the theme a data change.
- **Cookie-based sessions now**: deferred (see §7); the API already speaks
  Bearer tokens and the frontend follows.
- **Onboarding as a guarded route**: rejected — a route can be reached by URL,
  typed or linked, which is exactly what the consent rule forbids.
- **@axe-core/playwright for the accessibility check**: not possible in this
  sandbox (no browser binaries); `jest-axe` covers the same rules minus colour
  contrast.
