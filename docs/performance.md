# Performance on low-end devices

Everything on this page is either **measured** (with the command next to it) or
marked **not measured here**, with the reason. The audience is a phone bought for
under ₹12,000 on a 3G-to-4G connection, which is why the budgets below are in
gzip kilobytes and not in a Lighthouse score alone.

Measured on 2026-10-20 in the Day-49 sandbox (2 vCPU, 3 GB RAM, no GPU, no
browser) against the production build of commit `3f773ba`.

## 1. What ships on a first visit

`npm ci && npm run build && npm run budget` (frontend/):

```
initial JS     133.1 KB /   410.6 KB  (3 files)
initial CSS      6.7 KB /    27.6 KB
precache                    744.5 KB  (33 files, after first visit)
slowest route: /insights (256.5 KB, ~1.73 s)
All bundle budgets met.
```

| Budget | Allowance (gzip) | Measured | State |
| --- | ---: | ---: | --- |
| Initial JS (entry + static imports) | 135 KB | **133.1 KB** | met, 1.4 % headroom |
| Initial CSS | 10 KB | **6.7 KB** | met |
| Any single route increment | 25 KB | 11.6 KB (`/biofeedback`, the largest) | met |
| `/insights` increment (chart library) | 125 KB | **116.8 KB** | met, lazy and not precached |
| Service-worker precache (raw) | 800 KB | **744.5 KB** (33 files) | met |

The budget script walks the real import graph in `dist/.vite/manifest.json`, so a
static import that drags a heavy module into the entry chunk fails the build
rather than being averaged away. Its numbers come with a transfer estimate on
Lighthouse's mobile profile (1.6 Mbps, 150 ms RTT): the slowest route is
`/insights` at ~1.73 s of transfer, and every other route is ~1.15–1.21 s.

## 2. Heavy modules: lazily loaded, one chunk each

| Module | Weight | How it loads | Evidence |
| --- | --- | --- | --- |
| **WebXR / calm space** | 5.8 KB gzip route increment, 36 triangles, no 3D library | `CalmPage` is a lazy route; WebXR is requested at runtime only if `navigator.xr` exists; 2D canvas otherwise | `dist` route table above; `docs/verification/day46-xr-sensory.md` |
| **WebLLM + transformers.js (Private Mode)** | 30.9 KB gzip own chunk (96.5 KB raw), **not precached** | `PrivateModeProvider` is `lazy(() => import(...))` behind the Private Mode switch; the model runtimes themselves are *not* dependencies (injectable loader) | `frontend/src/features/chat/ChatView.tsx`, `ADR 0040`, budget output |
| **Voice (recorder + realtime)** | inside the `/chat` route chunk (12.7 KB gzip); WebAudio worklet created on demand | `ChatPage` is a lazy route; the recorder mounts on first use; the stream opens over the same-origin `/api` proxy with WebSocket upgrade | `frontend/src/features/chat/Composer.tsx`, `docs/verification/day40-realtime-voice.md` |
| **Knowledge graph / patterns** | 9.3 KB gzip route increment | `PatternsPage` is a lazy route; SVG only, no chart or graph library | `frontend/src/features/patterns/`, budget output |
| **Charts (insights)** | 107.8 KB gzip | `InsightsPage` is a lazy route and the chart chunk is deliberately excluded from the precache | budget output |

Rule that keeps this true: **a page is a route, not an import**. The only
statically imported feature in the shell is the safety bundle (rules + output
guard, 191 KB raw / ~30 KB gzip), because it has to work offline and before any
network call.

## 3. Startup budget

| Metric | Budget | Where it is enforced | Measured here |
| --- | --- | --- | --- |
| Initial JS, gzip | ≤ 135 KB | `npm run budget` (CI: Accessibility & Lighthouse job) | **133.1 KB** |
| Service-worker precache | ≤ 800 KB raw | same | **744.5 KB** |
| Largest Contentful Paint (mobile, slow 4G, 4× CPU throttle) | ≤ 2.5 s (warn) | `lighthouserc.json` via `npm run lighthouse` | not measured here — no browser |
| Total Blocking Time | ≤ 200 ms (warn) | same | not measured here |
| Cumulative Layout Shift | ≤ 0.1 (error) | same | not measured here |
| Lighthouse performance score | ≥ 0.90 (error) | same | not measured here |
| Third-party requests | 0 (error) | same | **0 by construction** — no analytics, no external script, fonts are the only cross-origin fetch |

The last committed browser measurement is Day 41, on the same class of machine
(2 cores, no WebGPU): **boot 92 ms, bundle evaluated in 4 ms, first chat turn
231.8 ms** (rules warm-up), later turns 30–38 ms, JS heap +3.4 MB after boot
(`docs/verification/day41-browser-private-ai.md`). Those numbers are from a
desktop browser on this sandbox's CPU, *not* from a ₹12,000 phone; they bound
what the JavaScript does, not what a low-end device will feel.

## 4. What is not measured, and why

1. **Lighthouse and Core Web Vitals.** No Chromium can be installed in this
   sandbox (the Playwright/Lighthouse browser CDN is not reachable; see
   `docs/verification/day49-verification.md`). The thresholds above are asserted
   by `.github/workflows/a11y-lighthouse.yml`, which runs `npm run lighthouse`
   against the production build on every push to `main`.
2. **Real-device startup** (cold start on a mid-range Android, PWA install,
   service-worker first-run cost). Needs hardware. The manual checklist is in
   §5.
3. **Private Mode with a real model.** No model bytes are pinned in this
   repository (no Hugging Face egress), so tokens/second, model load time and
   peak heap for on-device inference are unmeasured. `ADR 0040 §8` records the
   decision to ship templates until that measurement exists.

## 5. Manual checklist for a human with a low-end phone

1. Throttle to "Slow 4G" and 4× CPU in Chrome DevTools, then run Lighthouse
   mobile on `/`, `/chat` and `/calm`; compare against §3.
2. Install the PWA, close every tab, then open it offline: the shell, "Need help
   now" and the breathing exercises must work with no network.
3. Record a 30-second voice journal entry on the device microphone; check the
   permission prompt appears only after the button, and that stopping the
   recording is discoverable by a screen reader.
4. Open Private Mode on a device without WebGPU: the badge must say the reply
   came from templates, in the reader's language.
5. Measure first-turn latency on the slowest phone you can borrow, and write the
   number into `docs/verification/` — a budget nobody measured on the target
   device is a hope, not a budget.
