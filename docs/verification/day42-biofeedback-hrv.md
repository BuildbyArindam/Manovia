# Verification — Day 42 biofeedback breathing with wearables (HRV)

**Date:** 2026-10-16
**Scope:** the RR signal pipeline and its NumPy reference, artefact rejection,
RMSSD/HR windows and the quality gate, the adaptive pacer and its stability
simulations, Web Bluetooth integration, the session summary and encrypted
store, the CSV no-hardware path, the UI copy contract, and the bundle budgets.

Every number below was produced by running the command shown, in this sandbox,
on 2026-10-16. Where the sandbox cannot measure something (a real BLE radio, a
real chest strap, a real Firefox/Safari install), the row says so instead of
guessing. Implementation decisions: [ADR 0041](../adr/0041-biofeedback-hrv.md).

**Machine:** 2 vCPU, Node 22.22.3, Python 3.11.2, NumPy 2.4.6 (`/tmp/refvenv`).
Browser for e2e: Chromium 153.0.8010.0 headless (`@sparticuz/chromium`,
extracted from its npm tarball — Playwright's browser CDN is unreachable from
this sandbox), `LD_LIBRARY_PATH=/tmp/chromium-libs/lib`,
`PLAYWRIGHT_CHROMIUM_ARGS='["--no-sandbox","--disable-gpu"]'`.

## Summary

| # | Check | Command | Result |
|---|---|---|---|
| 1 | TS pipeline vs NumPy reference (fixtures, full series + every window) | `npx vitest run src/features/biofeedback/signal/reference.test.ts` | **PASS** — 8/8; max abs error 4.4e-10 (tolerance 1e-9) |
| 2 | Fixtures on disk match the generator | `/tmp/refvenv/bin/python scripts/day42_hrv_reference.py --check` | **PASS** — "fixtures and reference are up to date" |
| 3 | RR parsing, artefact rejection, RMSSD known values | `npx vitest run src/features/biofeedback/signal/` | **PASS** — 47/47 (4 files) |
| 4 | Pacer stability: 3 simulated users, 20 min, in-bounds, converges, no oscillation | `npx vitest run src/features/biofeedback/pacer/` | **PASS** — 14/14; trajectories printed below |
| 5 | Noisy fixtures suppress output (poor quality ⇒ null metrics) | part of #1 and #3 | **PASS** — poor windows (0.586, 0.35) show null RMSSD/HR in both TS and NumPy |
| 6 | CSV no-hardware path (units, timestamps, gaps, refusal messages) | `npx vitest run src/features/biofeedback/import` | **PASS** — 10/10 |
| 7 | Session summary + encrypted store (round trip, ciphertext at rest, tamper, AAD swap) | `npx vitest run src/features/biofeedback/session src/features/biofeedback/store` | **PASS** — 13/13 |
| 8 | Web Bluetooth layer: parsing, reconnect backoff, exhaustion, cancel, unsupported | `npx vitest run src/features/biofeedback/ble` | **PASS** — 9/9 against a fake GATT |
| 9 | UI strings: forbidden claims, key coverage, en/hi/bn parity | `npx vitest run src/features/biofeedback/ui/copy.test.ts` + standalone grep | **PASS** — 5/5; grep: 0 forbidden claims in 84 strings (3 negated disclaimers allowed by design) |
| 10 | Unsupported browser fallback (Firefox + Safari UA, no `navigator.bluetooth`) | `npx playwright test e2e/biofeedback.spec.ts` | **PASS** — honest path, no dead button, both UAs |
| 11 | e2e: CSV flow with every `/api` call blocked; fake BLE sensor + dizzy stop | same | **PASS** — 10/10 (5 tests × mobile-360x640 + desktop-1280x800) |
| 12 | Frontend suite + types | `npx vitest run`, `npx tsc --noEmit` | **PASS** — 2729 passed, 3 skipped (107 files); 0 type errors |
| 13 | Bundle budgets (incl. route increments and precache) | `npm run build && node scripts/check-bundle-budget.mjs` | **PASS** — "All bundle budgets met"; precache 612.1 KB of 800 KB |
| 14 | Backend lint | `make lint` (venv PATH) | **PASS** — ruff check clean, 408 files formatted, mypy clean (343 files) |
| 15 | Backend tests + coverage gate | `make test` | **PASS** — 4685 passed, 4 skipped; "coverage gate: all rules satisfied" |
| 16 | Real chest strap / real radio | — | **NOT MEASURABLE HERE** — manual checklist below |
| 17 | Real Firefox/Safari installs | — | **NOT MEASURABLE HERE** — covered by UA + feature-absence e2e; manual checklist below |

## 1. TS vs NumPy — the printed errors

```
[reference:clean-rsa] rmssdMs ts=20.92901906374762 ref=20.929019064 |err|=2.52e-10 ;
  meanHrBpm ts=75.19365028055694 ref=75.193650281 |err|=4.43e-10 ; qualityScore ref=1 |err|=0
[reference:noisy-ectopic-dropouts] rmssdMs ts=17.779627258153887 ref=17.779627258 |err|=1.54e-10 ;
  meanHrBpm ts=75.06142447988043 ref=75.06142448 |err|=1.20e-10 ;
  qualityScore ts≈ ref=0.882197515 |err|=2.43e-10
```

Plus every sliding window of both fixtures (metrics and the quality gate
decisions) at the same tolerance, and identical rejected-beat/gap indices
(exact integer comparison). Cross-language floats are compared at tolerance,
never deep-equal (JS float accumulation differs in the last ulp).

## 4. Pacer stability — the printed trajectories

```
[pacer:user 5 bpm]    every 2 min -> 2m:5.50  4m:5.00  6m:5.00  8m:5.00  10m:5.00  12m:5.00  14m:5.00  16m:5.00  18m:5.00  20m:5.00
[pacer:user 6 bpm]    every 2 min -> 2m:6.00  4m:6.00  6m:6.00  8m:6.00  10m:6.00  12m:6.00  14m:6.00  16m:6.00  18m:6.00  20m:6.00
[pacer:user 6.75 bpm] every 2 min -> 2m:6.50  4m:6.75  6m:6.75  8m:6.75  10m:6.75  12m:6.75  14m:6.75  16m:6.75  18m:6.75  20m:6.75
```

Assertions (all green): every rate within [4.5, 7.0]; every final-quarter rate
within 0.5 of the user's strongest rate; final-quarter total variation ≤ 0.6
and ≤ 3 direction changes. The pace converges by minute 4 and holds — no
hunting, no oscillation.

## 9. Forbidden claims — the grep

```
$ python3 (grep over en biofeedback strings; negation-aware)
  OK (negated disclaimer) wellnessBadge: ...— not a medical device...
  OK (negated disclaimer) wellnessNote: ...does not diagnose, treat, or monitor...
  OK (negated disclaimer) wellnessNote: ...not a medical instrument...
result: 0 forbidden claim(s), 84 strings checked
```

`ui/copy.test.ts` runs the same denylist over en/hi/bn with negation awareness
(disclaimers are required, not forbidden), pins identical keys and
`{{placeholders}}` across the three languages, and pins that every
`biofeedback.*` key referenced in code exists in `en`.

## 11. e2e — what the browser proved

```
$ PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/tmp/chromium ... npx playwright test e2e/biofeedback.spec.ts
  ✓ CSV path: import, session, summary and encrypted save with the API blocked
  ✓ summary copy makes no health claims (readable in the browser)
  ✓ fake BLE sensor: beats flow through the pipeline, dizzy stop ends it
  ✓ unsupported browser — firefox (linux): honest fallback and the CSV path
  ✓ unsupported browser — safari (macos): honest fallback and the CSV path
  5 passed   (both mobile-360x640 and desktop-1280x800 → 10/10)
```

The CSV test blocks **every** `/api` request and asserts none was attempted
(the only allowed `/api` traffic anywhere is the service worker's install-time
warm-up of the two public caches, and this run made none on the dev server).
The fake sensor injects real 0x2A37 packets (flags 0x16: contact detected +
RR present) and the test asserts "30 beats kept, 0 removed as artefacts" —
the whole packet → pipeline → UI chain in a real browser.

## 13. Budgets

```
precache  24 entries (612.12 KiB)      # budget 800 KB raw
slowest route: /insights (241.7 KB, ~1.66 s)
All bundle budgets met.
```

The hi/bn catalogue chunks are no longer precached (ADR 0025's on-demand
design, restored — see ADR 0041 §7); the biofeedback page is.

## Manual checklist (cannot be automated in this sandbox)

1. **Real chest strap** (Polar H10 or similar): pair over the browser chooser,
   confirm contact detection matches wearing the strap, take a 5-minute
   session and compare the summary's RMSSD against the vendor app (expect the
   same ballpark, not the same number — windowing differs).
2. **Reconnect**: walk out of range mid-session; the UI must show the
   reconnecting state and recover when back in range, or say it gave up after
   three tries.
3. **Firefox and Safari on real installs**: the unsupported panel must appear
   with the CSV alternative (expected: Web Bluetooth absent in both).
4. **iOS Safari / Android Chrome**: layout at 360 px, the dizzy stop reachable
   with one thumb during a session.
5. **A person with a real sensor and real dizziness**: press the stop during a
   session; everything must halt immediately and the summary must note it.
