# Day 46 verification — XR calm space, sensory profile, overwhelmed mode

Run on 2026-10-19 in the Arena sandbox, branch `arena/51d18eee-manovia`
(the session branch Arena pins; the requested `day-46-immersive-webxr-calm-space`
could not be created, same as Days 38–45). No main push.

This note is the evidence for the Day-46 brief and the honest boundary of what
this machine can do. Numbers below are from real runs on this sandbox; anything
that could not run here is labelled as such rather than inferred.

## What this sandbox cannot do

| Not available | Consequence | Covered instead by |
| --- | --- | --- |
| A WebXR headset or runtime | No live `immersive-vr` session | `src/features/calm/xr.test.ts` drives a hand-written `XRSessionLike` fake: reference space, per-eye views, one-gesture exit, cleanup |
| A GPU / WebGL driver | No real 3D pixels | `renderWebGL.test.ts` + `testGl.ts` recording context: 36-triangle budget, one upload, constant clear colour, shader-failure path |
| An audio output device | No audible pad | `audio.test.ts` drives the graph through a fake `AudioContext` |
| A downloadable Playwright browser | `npm run test:e2e` and browser `axe` do not run here | The Day-46 e2e specs are committed (`e2e/sensory.spec.ts`, `e2e/calm.spec.ts`) and must be run by you; Node-level axe (`jest-axe`) plus the existing suite ran |
| A mid-range phone | No on-device FPS | `npm run measure:frames` (CPU draw-call cost) and `--browser` mode for your machine |

## 1. Frame time of the 2D fallback

`npm run measure:frames` bundles the real `drawCalmFrame` painter with esbuild and
runs it against a recording 2D context (800×480, 1200 frames after 120 warm-up):

```
Calm-space 2D fallback — headless-cpu (1200 frames, 800x480)
  frame p50 : 0.004 ms  ->  260078 FPS
  frame p5  : 0.003 ms  ->  287687 FPS (5% of frames were slower)
  frame p95 : 0.013 ms
```

Read this honestly: those are the CPU costs of *issuing* the draw calls to a
recording context, not GPU work and not a display's frame rate. What it
establishes is that the painter allocates nothing, performs a bounded number of
calls per frame (under 100, asserted in `render2d.test.ts`) and cannot be the
bottleneck. The 2D scene draws ~26 shapes and one `arc`.

For the real number, run `npm run measure:frames -- --browser` on a machine with
a Playwright Chromium (or `npm run test:e2e` and read the `calm-space 2D rAF
intervals` line that `e2e/calm.spec.ts` prints). That mode reports rAF interval
p50/p5 from a real compositor.

**Budget (stated, enforced):** the calm route may add at most the generic
**25 KB gzip** route increment; the Day-46 build added **5.8 KB gzip** (route
`/calm`, `npm run budget`). Source-level budget in `budget.test.ts`: under 80 KB
of TypeScript for the whole feature, 36 triangles, no `fetch`, no image, no URL,
no 3D library.

## 2. Sensing profile and overwhelmed mode — automated checks

Vitest (Node/jsdom), all real assertions on rendered DOM and `<html>` attributes:

- `SensoryProvider.test.tsx` (7): defaults applied; every option writes the
  attribute and persists; the OS `prefers-reduced-motion` wins over the stored
  choice while the stored choice stays visible; overwhelmed forces
  `motion: none`, `sound: off`, `density: simple` and restores afterwards;
  session scope; corrupt storage repaired; works outside a provider.
- `SensorySettingsSection.test.tsx` (4): every choice is a labelled radio with a
  plain-language hint, applies immediately, persists, resets in one step; axe
  clean.
- `Overwhelmed.test.tsx` (5): one tap turns it on; the four destinations are
  `/calm`, `/exercises`, `/chat` and the helpline dialog; charts, badges and
  secondary copy are **removed from the tree** (not hidden with CSS); the
  current page stays; the full navigation disappears and returns; axe clean.
- `SensorySuggestion.test.tsx` (4): absent below the threshold; offered (not
  applied) at it; dismissible; axe clean.
- `arousal.test.ts` (7): the high-arousal rule, timestamps-only storage, the
  two-week window, the 30-day dismissal, and silence when storage is blocked.

**What is hidden in overwhelmed mode** (asserted, and listed in the UI):
charts (`InsightPanel` chart region, the patterns graph), badges
(`StreakIndicator`, `PrivateReplyBadge`), secondary copy
(`[data-sensory="secondary"]`), the side and bottom navigation.
**What stays:** the header, the crisis help button, the current page's text,
headings and controls, the four calm destinations, and the helplines dialog.

## 3. `prefers-reduced-motion`

- `useMediaQuery` now reads the media query **synchronously** for the first
  paint (Day 46). Before this, a reduced-motion reader got one animated frame
  before the effect corrected it — a real defect found while writing the calm
  space test.
- `SensoryProvider` computes effective motion as `osReduced ? "none" : choice`,
  so the profile can never out-vote the OS.
- CSS collapses every animation/transition under both
  `(prefers-reduced-motion: reduce)` and `[data-sensory-motion="none"]`
  (`tokens.test.ts` still asserts the reduced-motion block).
- `CalmSpace` draws a static orb and schedules no `requestAnimationFrame` under
  `motion: none`; a test asserts every `drawCalmFrame` call has
  `animateOrb: false`.
- XR entry is *not* removed by reduced motion — that would be an accessibility
  decision made on someone's behalf — but the scene inside VR is the same
  static-horizon, no-locomotion scene, and only the orb's scale moves.

## 4. WebXR feature detection and fallback

`detectVRSupport()` returns `unavailable` (no `navigator.xr`), `unsupported`
(`isSessionSupported` false) or `unsupported` when the call throws; none rejects.
`CalmSpace` renders the "VR isn't available in this browser…" fallback for both
of the latter and only shows "Enter VR" for `supported` (three tests). The XR
session requests `immersive-vr` and `"local"` only — no `local-floor`, no
locomotion input, and the source guard test forbids `axes`, `gamepad`, joystick
and teleport vocabulary in `xr.ts`.

## 5. XR code review — comfort checklist

Grepped `src/features/calm/` for `Math.random`, `requestAnimationFrame` in the
renderers, `translate`, `rotate`, `local-floor`, `bounded-floor`, `gamepad`,
`axes`, `fetch`, `http` — the assertions live in `renderWebGL.test.ts`,
`xr.test.ts`, `budget.test.ts`.

| Rule | Implementation | Evidence |
| --- | --- | --- |
| No artificial locomotion | No position state exists; XR camera is `XRView` only | `xr.test.ts` source guard; `renderWebGL.ts` has no camera variable |
| Stable horizon | `HORIZON_FRACTION` (2D), ground plane at `-SEATED_EYE_HEIGHT` (3D) | `render2d.test.ts` byte-identical landscape; `renderWebGL.test.ts` |
| No flashing / strobing | Constant colours, no `clearRect` in a 2D frame, one constant `clearColor` | `render2d.test.ts`, `renderWebGL.test.ts` |
| No sudden motion | Cosine breath, zero slope at phase joins; largest 60 fps step < 0.5 % | `breathing.test.ts` |
| One-gesture exit | `select`/`squeeze` → `end()`, once; page "Leave VR" button | `xr.test.ts` |
| 10-minute break prompt | `BREAK_AFTER_MS`, checked every 15 s; pauses only on request; ends VR so it is visible | `CalmSpace.test.tsx` |
| Who VR is not for | Pregnant, motion sickness/migraine, epilepsy/photosensitivity, age limits, seated-only, stop-if-dizzy, not therapy | `CalmSpace.test.tsx` asserts each phrase |
| Full 2D fallback | Canvas painter, no WebGL/WebXR/audio needed | `CalmSpace.test.tsx` + `render2d.test.ts` |

## 6. axe

Node-level `jest-axe` runs on: the sensory settings section, overwhelmed mode,
the suggestion card, the calm space, and (unchanged) the existing pages. All
clean.

Browser axe (`@axe-core/playwright`) is in `e2e/sensory.spec.ts` and
`e2e/calm.spec.ts` for Settings, overwhelmed mode and the calm space; **not run
in this sandbox** (no browser download). Run `npm run test:e2e` on your machine.

## 7. Full suites (final state)

- Frontend: `npx vitest run` — see PROGRESS.md for the final count; `tsc --noEmit`
  and `eslint .` clean; `npm run build` OK; `npm run budget` **met**.
- Backend: unchanged by Day 46; see PROGRESS.md for the final run.

## What you should test by hand

1. **A real headset** (Quest or similar): enter VR from `/calm`; confirm the
   horizon is level, nothing lurches, the orb only grows/settles, and a single
   trigger press returns you to the page. Note whether you feel any discomfort.
2. **Ten-minute prompt in VR**: sit for ten minutes; the session should end and
   the prompt be on screen, not missed.
3. **Audio**: turn sound on, confirm it is quiet, has no click when it starts or
   stops, and that `Settings → Sensory profile → Sound: off` disables the control.
4. **A mid-range phone**: run `npm run measure:frames -- --browser` and check the
   rAF p5 number while the calm space runs, then try the same on `/insights`
   with overwhelmed mode on and off.
5. **Screen reader**: toggle overwhelmed on a page with charts and confirm the
   navigation and chart region are gone from the reading order, and that
   "Need help now?" is still first in the header.
6. **Text size**: set the browser's own font size to Very Large, then the
   profile to "Larger" — both should compound, and nothing should clip.
