# Day 45 — Generative calm verification

Date: 2026-10-17. Branch: `arena/83b6e7a7-manovia`
(Arena-pinned; the brief's `day-45-…` branch name could not be
created — Arena ties the session to its branch, same as Days 38–44).
Decisions: [ADR 0045](../adr/0045-personalised-meditations.md).
All commands ran for real; every number below is actual output.
Only the offline fake LLM/voice providers were used — no network model
calls, no real voice model.

## Verification items (from the Day 45 brief)

| # | Item | Result |
| --- | --- | --- |
| 1 | 30 scripts across themes/durations/languages, lint results | **30/30 PASS, 0 forbidden-content findings** |
| 2 | Two full scripts printed for tone review | Done — `evals/reports/day45-script-{seated-calm-en,driving-focus-en}.txt` (inlined below) |
| 3 | Soundscape node graph + volume-limit assertion (mocked AudioContext) | **8/8 PASS** — graph dump `evals/reports/day45-engine-graph.json` |
| 4 | Playwright: safety preface + "stop instantly" controls | **Blocked in sandbox** (no Chromium — Playwright CDN outside egress allowlist). Content covered by jsdom view tests (5 files / 27 tests) + real-HTTP API e2e (10/10); `e2e/meditation.spec.ts` written for a machine with a browser |
| 5 | Offline operation after first load | **Static PASS** (all route chunks in the SW precache); runtime network-off reload is a manual check (no browser in sandbox) |
| 6 | No copyrighted audio assets | **PASS — zero audio files** in the working tree or in git |

## 1. The 30-script eval

```sh
cd backend && ../.venv/bin/python ../evals/meditation/run_meditation_eval.py
# report: evals/reports/2026-10-17/meditation_eval.json
```

**30/30 passed. Forbidden-content findings: 0.** Matrix: 6 themes
(morning, focus, stress-release, calm, sleep, gratitude) × en/hi/bn ×
180 s/300 s/600 s/900 s with seeded variety; each script linted for
forbidden patterns (trance/dissociation inductions, medical claims,
promises, eyes-closed instructions where unsafe), length, pacing and
structure (safety preface, stop cue, breath cues, closing).

Sizing is honest, not padded: when the curated pool cannot fill a long
offline script, the estimate is disclosed (`estimated_duration_s`)
rather than padded with filler — e.g. `sleep-en-900s` renders 607 words
≈ 343 s. The eval's floor is `min(0.75 × budget, curated-pool max)`,
matching the generator's own contract (ADR 0045).

## 2. Two full scripts (tone review)

Written by `evals/meditation/verify_api_e2e.py` from the live API:
`evals/reports/day45-script-seated-calm-en.txt` (320 words) and
`evals/reports/day45-script-driving-focus-en.txt` (345 words). Both
carry: safety preface (the driving one: *"Please do not use this
practice while driving. If you are driving, pull over somewhere safe
and stop before you begin"*), stop cue, eyes-open instruction (no
eyes-closed text anywhere in the driving script). Warm, plain, short,
non-judgemental; no promises (*"You do not need to feel calmer for this
to be worth it"*), no medical claims. One copy nit found in tone review
and fixed: a run-on in the curated preface
(*"stop at any time there is nothing to finish"* → semicolon) in
`backend/app/content/meditation/en.yaml` (three places).

## 3. Soundscape engine — node graph + volume limit

```sh
cd frontend && npx vitest run src/features/meditation/soundscape/engine.test.ts
# → 8 passed
```

Graph dump (full JSON in `evals/reports/day45-engine-graph.json`),
`warm-dawn` palette after `start()`:

```
osc-1 → pad-filter        osc-2 → pad-filter
osc-3 → pad-filter        osc-sub → pad-filter      (sine chord + sub)
pad-filter → pad-gain → master
noise-source → noise-filter (bandpass) → noise-gain → master
breath-lfo → lfo-amp → pad-filter.frequency   (AudioParam modulation)
swell-lfo → swell-amp → pad-gain.gain         (AudioParam modulation)
master → limiter (−12 dB, 6 dB knee, 12:1) → destination
master: ceiling 0.2 (MAX_MASTER_GAIN), currentGain 0.1 @ volume 0.5
```

Volume-limit assertions (in the test): hostile `setVolume(5)` clamps the
ceiling to `MAX_MASTER_GAIN` (0.2); the post-fade gain never exceeds the
ceiling; the limiter in front of the destination is the soft ceiling.
Adaptation is bounded (`pace ∈ [4, 12]` bpm, monotone slow drift;
brightness inside the palette's cutoff range). Reduced-stimulation mode
cuts texture level ×0.4, breath depth ×0.5, drift rate. Texture is a
single generated 2 s looping buffer — no assets.

**Regression found here:** the first wiring sent the pad oscillators
straight to the pad gain (the lowpass was connected downstream with no
input), and both LFOs connected to node *inputs* instead of
`AudioParam`s — the brightness control and the swell would have done
nothing audible (the swell would have summed a breathing-rate sine into
the pad). Fixed in `start()` (oscs → `pad-filter`; `lfo-amp →
pad-filter.frequency`; `swell-amp → pad-gain.gain`) with explicit edge
assertions in the graph test, including `not.toContainEqual` guards for
the old buggy edges.

## 4. Playwright (browser e2e) — blocked in sandbox

The sandbox has no Chromium binary and `npx playwright install chromium`
fails (the Playwright CDN is outside the egress allowlist). Per the
standing instruction, the fallback is:

* **Safety preface + stop cue in the rendered UI** — covered by
  `MeditationView.test.tsx` (jsdom; 5 test files / 27 tests in
  `src/features/meditation`, all passing): preface chips, stop chip,
  stop-now control, eyes-open variant, driving warning.
* **The API contract** — covered by real HTTP in
  `evals/meditation/verify_api_e2e.py`: **10/10** (compose ×3,
  determinism, 422 closed-eyes-while-driving, real WAV render
  7,936,118 B `provider=fake`, cache miss→hit with identical bytes,
  422 `script_hash_mismatch` on tampered sections, captions with
  `timing_basis=estimated`).
* **`frontend/e2e/meditation.spec.ts`** is written for a machine with a
  browser: `cd backend && LLM_PROVIDER=fake make dev`, then
  `cd frontend && npx playwright test e2e/meditation.spec.ts` (4 tests:
  preface + stop cue, play → "Stop now" → instant return to idle,
  eyes-open variant, driving note + no eyes-closed text).

## 5. Offline operation after first load

Static check (real build, `npm run build`):

```
precache  28 entries (641.32 KiB)
```

`dist/sw.js` precaches every route chunk — verified
`MeditationPage-*.js` (the meditation page, lazy chunk) and
`BiofeedbackPage-*.js` are in the precache list; the hand-written
service worker (`src/sw/sw.ts`, `injectManifest`) covers
`**/*.{js,css,html,svg,png,webmanifest}`. After the first online load,
the meditation page (and its API-less composition path) is served from
the cache; audio rendering needs the API, as designed (nothing is
stored offline). **Manual check for the user:** load `/meditation`
online, enable "offline" in DevTools, hard-reload — the page and
library must come back.

## 6. Copyrighted audio assets

```sh
git ls-files | grep -iE '\.(mp3|wav|ogg|oga|aac|m4a|flac|opus|mp4|webm|mid|midi|aiff?)$'
# → (empty)
```

Zero audio files in the working tree (outside `node_modules`/`dist`) or
in git. The soundscape is 100 % procedural WebAudio (oscillators + one
generated noise buffer); the voice is rendered at runtime by the server
TTS (fake provider in CI; Piper when configured) — no samples, no
model weights, no licences needed. No `.onnx`/`.tflite`/`.pt`/`.bin`
model files are tracked either (`src/features/private/models` is
TypeScript code for the runtime private-model download manager).

## Defects found and fixed during verification

| Defect | Root cause | Fix + regression |
| --- | --- | --- |
| Tampered section text rendered and spoken (API returned 200 where the brief demands 422) | The content hash covered the display-only `transcript` field, but the renderer reads the **sections** | `meditation-v2` hash basis covering every section's role/source/text/pause; render-cache key derived from that hash; `test_tampered_sections_change_the_content_hash` (unit) + `test_audio_rejects_a_tampered_section` (integration); live e2e now 10/10 |
| Pad lowpass bypassed; LFOs wired to node inputs instead of AudioParams | `start()` connected oscs → `pad-gain`, `lfo-amp` → filter node, `swell-amp` → gain node | Re-wired (see §3) with edge assertions incl. `not.toContainEqual` for the old edges |
| `auto` theme resolution flaky depending on wall-clock time | `resolve_theme` fell back to `datetime.now(UTC)` instead of the injected clock | Clock injected (`now=` parameter, generator's `self._now`); the existing test is now deterministic |
| Curated preface run-on (3×) | Missing punctuation in `en.yaml` | Fixed; eval + e2e re-run green |
| `make lint` red: 5 files unformatted, 31–45 mypy strict errors (pre-existing, surfaced by the rebuilt toolchain) | Formatting drift + untyped test parameters in Day 45 test files | `ruff format` + proper annotations (`Path`, `Settings`, `Literal`, `Sequence[LLMMessage]`, `Generator`, `NoReturn`, None-guard) — no `noqa` waivers added, no test weakened |

## Final gauntlet (see wrap-up message for the fresh runs)

* backend: `pytest` full suite + Day 26 coverage gate (all rules
  satisfied; overall 93.9 % stmt / 85.3 % br); `ruff check` +
  `ruff format --check` clean (448 files); `mypy app tests` clean
  (382 files)
* frontend: `vitest run` 2,773 passed / 3 skipped (114 files);
  `tsc` (app + SW configs) clean; `eslint` clean; `npm run build`
  exit 0 with all bundle budgets met
