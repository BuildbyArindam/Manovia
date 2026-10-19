# Documentation assets

Reserved for documentation images and other static assets. Do not place private
or identifying user data here.

## Day 12 chat screenshots — generated 2026-10-11 in real Chromium 153

The Day 12 Playwright suite writes these six files. They were **generated during
the Day 21 verification run** with real Chromium 153.0.8010.0 (obtained from npm's
`@sparticuz/chromium@153.0.0`, because `cdn.playwright.dev` is unreachable from the
build sandbox; see [docs/verification/day21-daily-plan-week-3-review.md](../verification/day21-daily-plan-week-3-review.md))
against the live API and dev server. To regenerate them on a machine with browsers:

```bash
# 1. the API, with the offline Fake model
cd backend
LLM_PROVIDER=fake SAFETY_ML_ENABLED=false APP_ENV=development \
SECRET_KEY=dev-only FIELD_ENCRYPTION_KEY=$(python -c \
  'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())') \
  make dev          # after `alembic upgrade head`

# 2. the browser tests, which write the screenshots below
cd frontend && npm run test:e2e
```

| file | what it shows |
| --- | --- |
| `day12-chat-mobile-360x640.png` | an ordinary exchange at 360x640, light palette |
| `day12-chat-dark-mobile-360x640.png` | the same conversation in dark mode |
| `day12-chat-crisis-mobile-360x640.png` | a HIGH-risk reply: the CrisisCard under the message, page softened |
| `day12-chat-desktop-1280x800.png` | the desktop layout (side rail), light palette |
| `day12-chat-dark-desktop-1280x800.png` | the same, dark palette |
| `day12-chat-crisis-desktop-1280x800.png` | the crisis card at desktop width |

The screenshots contain only synthetic conversation text (the three starter
sentences and `I want to kill myself`, used as the safety test phrase) and only
the synthetic `555…` / international helpline entries the shipped content file
serves for the `DEFAULT` region. No real person's words, ever.

## Day 21 home-screen screenshots — expected, not yet generated

The Day 21 home screen (greeting, plan card, quick mood check-in, chat link,
"Need help now?") has no screenshots in the repository yet, for the same reason
as Day 12: the sandbox that built Day 21 has no browser binary. `e2e/home.spec.ts`
asserts behaviour against a running API but captures no images. Generate them on
a machine with browsers:

```bash
# 1. the API, with the offline Fake model
cd backend
LLM_PROVIDER=fake SAFETY_ML_ENABLED=false APP_ENV=development \
SECRET_KEY=dev-only FIELD_ENCRYPTION_KEY=$(python -c \
  'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())') \
  make dev          # after `alembic upgrade head`

# 2. the frontend dev server
cd frontend && npm run dev

# 3. screenshots of http://localhost:5173 (complete onboarding as a guest first)
cd frontend && npx playwright screenshot --viewport-size=360,640 \
  http://localhost:5173 ../docs/assets/day21-home-mobile-360x640.png
npx playwright screenshot --viewport-size=1280,800 \
  http://localhost:5173 ../docs/assets/day21-home-desktop-1280x800.png
```

| file | what it shows |
| --- | --- |
| `day21-home-mobile-360x640.png` | the home screen at 360x640: greeting, plan card, quick check-in, light palette |
| `day21-home-dark-mobile-360x640.png` | the same in dark mode |
| `day21-home-desktop-1280x800.png` | the desktop layout (side rail with Home), light palette |
| `day21-settings-plan-1280x800.png` | the Daily plan settings section (exercise preferences + quiet hours) |

Screenshots show only synthetic guest data: a first-visit plan ("Take a first
check-in") or a plan generated from synthetic moods created through the app.
No real person's words, ever.

## Day 25 screenshots — generated 2026-10-12 from the production build

Captured by `frontend/e2e/day25/screenshots.spec.ts` in Chromium 153 (the same
`@sparticuz/chromium` binary as Day 21, see above) against the **production
build** served by `vite preview` and the live API with the Fake model — the
service worker, and therefore the offline states, only exist in that build.
The spec is skipped unless `CAPTURE_SCREENSHOTS=1`, so CI never rewrites these
files. To regenerate them:

```bash
# 1. the API with the offline Fake model, on :8000 (as above)
# 2. the production build with the same-origin /api proxy
cd frontend && npm run build && npx vite preview --host 0.0.0.0 --port 4173
# 3. in another terminal
cd frontend && CAPTURE_SCREENSHOTS=1 npx playwright test -c playwright.prod.config.ts screenshots
```

| file | what it shows |
| --- | --- |
| `day25-home-mobile-360x640.png` | the home screen at 360x640 after onboarding, with offline support ready: greeting, quick check-in, bottom navigation |
| `day25-offline-help-mobile-360x640.png` | "Need help now?" opened with the network off: the "saved on this device" note above the cached helplines |
| `day25-offline-checkin-mobile-360x640.png` | the confirmation after a check-in made offline: kept on this device, sent the next time Manovia is open and online, with the way to the helplines |
| `day25-settings-high-contrast-desktop-1280x800.png` | Settings at 1280x800 with the high-contrast palette on (7:1 text pairs) |
| `day25-exercises-dark-high-contrast-desktop-1280x800.png` | Exercises in the dark high-contrast palette |
| `day25-accessibility-statement-desktop-1280x800.png` | the accessibility statement (`/accessibility`) |

Only synthetic content appears: a fresh guest account, the made-up note
`A made-up note, written offline.`, and the helpline entries the shipped
content file serves for the `DEFAULT` region. No real person's words, ever.
`day25-offline-checkin-mobile-360x640.png` was re-captured during Day 25
verification after its copy was corrected (see PROGRESS.md).

## Social preview (Day 30)

`social-preview.png` (1280 × 640) is the GitHub social preview image, an
original flat design generated for the launch and resized to GitHub's
recommended dimensions. Upload it by hand: repository **Settings → General →
Social preview → Edit** (the API does not expose this setting).

## Day 46 calm-space preview — generated from the app's own painter

`day46-calm-space.svg` is produced by running the product's `drawCalmFrame`
painter (the 2D fallback in `frontend/src/features/calm/render2d.ts`) against an
SVG-writing context; `day46-calm-space.png` is that SVG rasterised with resvg.
It is **not** a browser screenshot — no browser can run in the Day 46 sandbox.
The layout, palette and breath position come from the shipping code.

```bash
cd frontend
node scripts/render-calm-preview.mjs          # writes ../docs/assets/day46-calm-space.svg
```

Replacing it with a real screenshot (recommended when a browser is available):
run `npm run test:e2e` with the Day 46 specs, or open `/calm` in the production
build and capture at 960 × 540. Keep the caption honest about which it is.
