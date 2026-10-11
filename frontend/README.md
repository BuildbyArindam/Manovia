# Frontend

The Manovia web client: React 18 + Vite + TypeScript (strict) + Tailwind, with
React Router, TanStack Query, Vitest, Testing Library, ESLint and Prettier.

Day 5 ships the skeleton: the design system (tokens), the layout with its two
navigations, the onboarding gate, the crisis helpline dialog, and the typed API
client. Chat, mood, journal, exercises and insights are placeholders with an
honest "coming next" — they arrive with their safety gates, not before.

## Setup

```bash
cd frontend
npm ci            # install exactly what package-lock.json pins
npm run dev       # http://localhost:5173
```

The dev server proxies `/api` to `http://127.0.0.1:8000` (the FastAPI backend,
`make dev` in `backend/`). Point it somewhere else with
`VITE_API_PROXY_TARGET=http://host:port npm run dev`, and change the client's
base URL with `VITE_API_BASE_URL`. The browser only ever talks to its own
origin.

## Commands

| Command                | What it does                                             |
| ---------------------- | -------------------------------------------------------- |
| `npm run dev`          | Vite dev server with HMR                                 |
| `npm run build`        | Type-check, then a production build into `dist/`         |
| `npm run preview`      | Serve the production build                               |
| `npm run typecheck`    | `tsc --noEmit` (strict, plus `noUncheckedIndexedAccess`) |
| `npm run lint`         | ESLint (flat config, typescript-eslint, react-hooks)     |
| `npm test`             | Vitest in watch mode                                     |
| `npm run test:run`     | Vitest, once                                             |
| `npm run format`       | Prettier, write                                          |
| `npm run format:check` | Prettier, check                                          |

## Layout

- `src/index.css` — **the design tokens.** Two palettes (warm paper / warm
  charcoal) as `--manovia-*` custom properties, swapped by `data-theme` on
  `<html>`, plus the base styles, the skip link and the `prefers-reduced-motion`
  block. Every colour in the app comes from here.
- `src/styles/tokens.test.ts` — parses that CSS and asserts ≥ 4.5:1 for every
  text pair in both themes, ≥ 3:1 for the focus ring and strong borders, a 16px
  base font size, and a real reduced-motion block.
- `src/lib/api.ts` — the API client: error envelope, token refresh (single
  flight), one replay, injectable token store and clock.
- `src/lib/endpoints.ts` — typed endpoint wrappers and wire shapes.
- `src/onboarding/` — the flow, its provider (which owns the consent gate), its
  storage, and its copy.
- `src/components/` — `AppShell` (landmarks, skip link, responsive navigation),
  `Header` (with the persistent "Need help now?" button), `Modal` (the focus
  contract), `CrisisResourcesModal`, the two navigations, icons.
- `src/pages/` — the six surfaces plus a 404. Only Settings is real today
  (palette, AI disclosure, account summary).
- `src/theme/ThemeProvider.tsx` — palette choice: light, dark, or follow the
  system.
- `src/test/` — render helpers, the `matchMedia` stub, and the `jest-axe` types.

## Accessibility floor

These are asserted in the test suite, not left to review:

- a skip link as the first focusable element, and `header` / `nav` / `main` /
  `footer` landmarks;
- a real focus trap in every dialog, with focus restored to whatever opened it;
- the helpline dialog reachable by keyboard from every route;
- contrast ≥ 4.5:1 for text and ≥ 3:1 for UI boundaries, in both themes;
- `prefers-reduced-motion` honoured in CSS _and_ in JS
  (`usePrefersReducedMotion`);
- step changes in onboarding move focus to the new heading and are announced in
  a polite live region.

## Known issues

- The refresh token lives in `localStorage` (XSS-readable). An httpOnly cookie
  is a backend change; see [ADR 0005](../docs/adr/0005-frontend-skeleton.md).
- No browser-level test in CI: this repository's sandbox cannot download a
  Playwright browser, so the served app is checked with curl and the rendered
  tree with jsdom (`jest-axe` disables colour-contrast rules in jsdom, which is
  why contrast is a unit test on the tokens).
- The helpline list is placeholder content pending human verification (Day 8).
