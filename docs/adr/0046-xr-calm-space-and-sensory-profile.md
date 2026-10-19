# ADR 0046 — A calm space that cannot surprise anyone, and a sensory profile app-wide

Date: 2026-10-19. Status: accepted.

## Context

Day 46 adds two things that touch every screen. The first is an immersive calm
space: a seated, still, low-poly scene with a breathing orb, optional procedural
audio and — where the browser and a headset can actually do it — WebXR. The
second is a *sensory profile*: motion, sound, density, colours and text size,
applied across the whole app, plus a one-tap "I'm overwhelmed" mode.

Both are safety surfaces in a way that a chart is not. A calm space whose
horizon drifts, whose camera moves, or whose orb flashes is not a calm space; it
is a vestibular and photosensitivity hazard with breathing instructions attached.
A settings panel that only changes one page is a promise the interface does not
keep. The decisions below are the ones that keep those two claims true as the
code changes.

## Decisions

### 1. No 3D library. A hand-written WebGL renderer, 36 triangles, no assets

Three.js or A-Frame would be the conventional choice and both are excluded by
the brief's own budget: no external CDN assets at runtime, and a stated size.
The scene is a ground plane, a far ridge, a mid ridge, four low-poly firs and an
octahedral orb — **36 triangles**, one program, one static vertex buffer,
created once and never re-uploaded. The calm route's own chunk measured **5.8 KB
gzip** in the Day-46 build (budget: the generic 25 KB route increment).

The larger reason is reviewability. A 200-line renderer with no dependency can be
read end to end by somebody asking "can this hurt me?" — and `renderWebGL.test.ts`
asserts the properties that answer it: one constant clear colour, a fixed seated
eye height, no `Math.random`, no camera animation, no locomotion input, and the
headset's own pose as the only camera source.

Rejected: adding `three` (≈600 KB raw for a scene this size); shipping baked
images or a video loop (they look better, cost more, and a video cannot breathe
at the user's pace); a CSS-only 2D fallback (it cannot do the WebXR path at all
and would be two visual languages).

### 2. The fallback is the primary path, not the degraded one

WebXR is feature-detected three ways — `navigator.xr` missing, `isSessionSupported`
answering false, and `isSessionSupported` *rejecting* (no secure context, no
headset, permission refused) — and all three resolve to the 2D canvas with an
honest sentence, never an error. VR is an enhancement offered only when the
browser says yes; the 2D scene is what most people will see, and it is what the
frame-time script measures.

The 2D painter draws through a structurally typed `Canvas2DLike`, which is what
makes it measurable in Node against a recording context without a GPU. That is
also how comfort is tested: the hills and the horizon must be byte-identical
frame to frame, the orb's *radius* may change and its *colour* may not, and
there is no `clearRect` in the frame (a clear is a flash waiting to happen).

### 3. Comfort rules are structural, not stylistic

- **No artificial locomotion.** There is no position variable to animate. The
  WebXR camera comes from `XRView` only; the 2D scene never pans.
- **Stable horizon.** `HORIZON_FRACTION` (2D) and a constant ground height at a
  seated eye height (3D) are constants, and the tests pin them.
- **No flashing.** Colours are constant. The only animated quantity is the orb's
  size, on a single cosine over a ten-second cycle whose largest 60 fps step is
  under 0.5 % of its range (asserted).
- **No sudden motion.** The breath curve is continuous with zero slope at both
  phase boundaries; `motion: none` (or the OS setting) draws one static frame.
- **One-gesture exit.** Any `select` or `squeeze` ends the session, once, with
  listeners removed; the page also has a "Leave VR" button.
- **Ten-minute break prompt.** Non-blocking; it pauses only if the person
  chooses "Take a break", and in VR it ends the session so the prompt is
  actually visible rather than unheard.
- **A who-this-is-not-for note before the entry point**: headset-maker age
  limits and guidance, pregnancy, motion sickness and migraine, epilepsy and
  photosensitivity. It is a paragraph, not a modal that can be dismissed
  unread.

### 4. Sound is procedural, opt-in, and follows the breath only in gain

Three detuned sines through a low-pass filter; the breathing curve modulates one
gain parameter with `setTargetAtTime` (no step, so no click). No sample, no
loop point, no melody, no percussion. It starts only from a click, stays below a
master gain of 0.06, closes its context on stop, and the profile's `sound: off`
*disables the control* rather than silently ignoring it.

### 5. One sensory profile, applied through `<html>` attributes and tokens

The provider computes the **effective** values and writes them as attributes:
`data-sensory-motion` (effective), `data-sensory-motion-choice` (what the person
picked), `data-sensory-sound`, `data-sensory-density`,
`data-sensory-colours`, `data-sensory-font`, `data-overwhelmed`. CSS then does
the work the design tokens already do for themes:

- **Motion `none`** collapses every animation and transition (the same rule as
  `prefers-reduced-motion`, and the provider computes `none` when the OS asks
  for it, so the two can never disagree). **Low** stops looping/drifting
  animation but keeps short state changes.
- **Density `simple`** hides secondary copy (`[data-sensory="secondary"]`), never
  headings, actions or values.
- **Colours** *reuses the Day-25 high-contrast palette* under a second selector
  rather than inventing a third palette; a test asserts the two selectors
  resolve to the same tokens.
- **Text size** multiplies the existing `max(100%, 16px)` base, so the reader's
  browser size still wins and text can never shrink below 16 px.

Stored in `localStorage` (versioned, repaired field by field, silent when storage
is blocked). No server call, no account, no identifiers: a display preference is
not a health record.

### 6. Overwhelmed mode hides in React first, CSS second

Charts are removed at render (`InsightPanel` does not mount the chart, so it is
never measured or fetched), badges return `null` (`StreakIndicator`,
`PrivateReplyBadge`), and the full navigation is replaced by four destinations —
breathe (`/calm`), ground (`/exercises`), talk (`/chat`), help (helplines). The
CSS rule (`[data-overwhelmed="true"] [data-sensory="chart"]`) is the second line
of defence for markup outside the provider, and the e2e spec asserts both.

Deliberately **not** hidden: the crisis help button, the current page, plain
text, the data tables behind insights. Hiding a way to get help would be the
opposite of the point.

The mode is session-scoped (`sessionStorage`), so closing the tab restores the
normal interface; the stored profile is untouched while it is on.

### 7. The suggestion is an offer, counts timestamps only, and is rare

Three check-ins in fourteen days with **energy ≥ 4 and valence ≤ 2** (activated
*and* unpleasant) is the whole signal. Only timestamps are stored locally —
never the emotion labels, tags or notes. The app never switches modes by itself;
it offers a card with "Use the simpler view" and "Not now", and a dismissal
silences it for 30 days. A heuristic for "this might be a lot right now" is not
a diagnosis, and the copy says what was noticed rather than naming a feeling.

### 8. Settings is where it lives; the calm space is its own route

`/calm` is lazy, in the navigation, and reachable from Settings and from the
overwhelmed bar. It is not bolted onto the meditation page: the meditation
surface composes spoken scripts and a soundscape, and mixing the two would make
both harder to reason about.

## Consequences

- **Good:** the calm space cannot regress into a locomotion demo without failing
  a test that names the rule; the profile is one attribute write away from every
  surface, so a new page inherits it; overwhelmed mode removes cost as well as
  pixels.
- **Cost:** a hand-written renderer is more code to own than `three` would be
  (about 400 lines, all reviewed), and it will not grow into a general 3D engine.
  The 2D painter is deliberately static, so "more beautiful" means "more draw
  calls", which the frame-time script will show.
- **Cost:** hiding secondary copy in `simple` density can hide a caveat that a
  page considered part of its explanation. The wrappers are explicit and
  reviewed per surface, and the caveats that matter (safety notes, caveat blocks
  in summaries) are not wrapped.
- **Not claimed:** no headset, WebGL, GPU or audio device exists in the build
  sandbox. The XR path is verified against a fake session; the audio path
  against a fake context; frame times are CPU-side in Node unless
  `--browser` is run on a real machine. Real-device comfort is a human test and
  is listed in `docs/verification/day46-xr-sensory.md`.

## Rejected alternatives

- **Forcing reduced motion whenever motion is `none` for everyone.** Some people
  want the breathing orb and nothing else moving; the profile lets them say so
  without taking away the cue.
- **A modal "are you overwhelmed?" question.** Asking somebody to classify their
  state to get a simpler screen is one more demand at the worst moment. The
  control is a persistent toggle with plain words.
- **Server-side sensory profiles.** Sync would be nice across devices, and it
  would also turn a display preference into an account-level record of
  sensitivity. Local only, and exported nowhere.
- **Auto-entering overwhelmed mode after high-arousal check-ins.** The brief
  says never forced; more importantly, an interface that suddenly changes shape
  is itself a surprise.
