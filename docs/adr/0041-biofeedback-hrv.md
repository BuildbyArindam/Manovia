# ADR 0041 — Biofeedback breathing with wearables: measured HRV, a probe-and-settle pacer, and honest withholding

Date: 2026-10-16. Status: accepted.

## Context

Day 42 asks for a breathing pacer that adapts to heart-rate variability (HRV)
from a wearable, with a session summary, trends, and a no-hardware CSV path —
a **wellness** feature that must never look like a medical device (AGENTS.md
rule 1), must keep data local unless a summary is explicitly saved (and then
encrypted), and must ship with tests including a NumPy reference comparison and
pacer stability simulations.

Four constraints shaped the design:

1. **Numbers must deserve trust or be withheld.** HRV computed from motion-
   corrupted or contact-lost signal is worse than no number: it invites
   conclusions from noise. The quality gate has to be part of the measurement,
   not a decoration, and it must refuse to display when the signal is poor.
2. **An adaptive pacer must be stable and explainable.** A rate that wanders
   makes people dizzy — literally the failure the "I feel dizzy — stop" button
   exists for. The adaptation algorithm must be documented, bounded, provably
   convergent in simulation, and always user-overridable.
3. **This sandbox has no Bluetooth radio and no real chest strap.** Web
   Bluetooth integration must be fully unit-tested against fakes, with a real
   browser e2e driven by an injected `navigator.bluetooth` stub, and an honest
   manual checklist for the radio itself.
4. **Cross-language correctness needs an oracle.** TypeScript floating-point
   behaviour must be validated against a NumPy reference on recorded fixtures
   (the `scripts/day42_hrv_reference.py` convention, continued from earlier
   days), so a refactor cannot silently change the physiology.

## Decision

### 1. The signal pipeline is a fixed sequence with a quality gate inside

`features/biofeedback/signal/` implements one pipeline, used identically by the
live sensor and the CSV import:

1. **RR extraction** (`rr.ts`): the Bluetooth Heart Rate Measurement packet
   (0x2A37) carries flags, an optional 8/16-bit bpm, contact bits, and RR
   intervals in 1/1024 s units. `parseHeartRateMeasurement` rejects malformed
   packets (odd byte counts, intervals of 0) with a typed error; the UI counts
   them as malformed and never shows them as data. `RRStreamAccumulator`
   timestamps beats cumulatively from the intervals themselves (arrival times
   only locate **dropouts**: >3000 ms of notification silence sets
   `breakBefore` on the next sample and increments a dropout counter).
2. **Artefact rejection** (`artefact.ts`): out-of-range intervals
   (<250 or >2300 ms), physiologically implausible jumps (|Δrr| > 750 ms), and
   ectopic beats (local-median deviation >20 %, with the pair around them
   broken) are rejected with reasons recorded; gaps from packet silence insert
   a `Gap` with an estimated missing-beat count.
3. **Windowed metrics** (`metrics.ts`): sliding (start, end] windows of 60 s
   stepping 30 s, anchored at first-beat − 60 s so early partial windows are
   honestly partial. RMSSD = √(mean Δrr²) over successive accepted pairs
   **whose later sample does not cross a removal or a gap** (see the deviation
   note below); mean HR = 60000 ÷ mean(rr). Fewer than 10 pairs → null.
4. **Quality** (`quality.ts`): score = 0.65 × artefactScore + 0.35 × gapScore,
   capped at 0.35 for contact-not-detected and for flatline (non-break pairs
   with |Δrr| < 1 ms exceeding 80 % of pairs); <15 beats → poor outright.
   Levels: good ≥ 0.85, fair ≥ 0.65, poor < 0.65. **Poor ⇒ rmssdMs and
   meanHrBpm are null** — the numbers are withheld, never estimated.

**Deviation from the textbook RMSSD interpolation.** When beats are removed,
common practice interpolates across the hole so the series stays uniform. We
skip affected pairs instead: interpolation fabricates beat times and therefore
fabricates the very variability we claim to measure. The cost is fewer pairs
per window; the benefit is that every number is computed only from observed
beats. Both the TS code and the NumPy reference implement the same skip rule.

The NumPy reference (`scripts/day42_hrv_reference.py`) mirrors every constant
and formula 1:1, regenerates the committed fixtures (seeded LCG — deterministic),
and `--check` proves the fixtures on disk match. `signal/reference.test.ts`
compares the TS pipeline against `reference.json` at 1e-9 with the absolute
errors printed (largest observed: 4.4e-10).

### 2. The pacer probes and settles; it does not follow every fluctuation

`pacer/` implements **probe-and-settle**, bounded to 4.5–7.0 breaths/min,
default 6.0, adapting at most every 30 s by at most 0.25 breaths/min:

1. **Measure**: the HR-oscillation amplitude at the current breathing rate
   (`hrOscillation.ts`: 4 Hz resampling clamped to real data coverage,
   Hann-windowed single-bin Goertzel at the pacing frequency) over the stretch
   since the last move, capped at 120 s. Measuring the since-move stretch — not
   a smeared trailing window — makes amplitudes at different rates comparable
   and avoids flat-edge artefacts from sparse early history.
2. **Probe**: two fixed probes (+0.5, −0.5 from the starting rate), walked with
   ≤ maxStep per adaptation and measured at every step.
3. **Fill**: 0.25-spaced neighbours around the best measured rate, alternating
   sides, each visited once.
4. **Settle**: walk to the best measured rate and hold ("held-peak"). A move
   that would leave the bounds is reported as "held-boundary" (the clamp ate
   it) rather than silently applied.

Every adaptation emits a decision (`moved | held-peak | held-boundary |
held-quality | held-data | held-no-measurement | held-manual | held-stopped |
held-not-due`), so the pacer's behaviour is auditable from its own events.
Poor quality or an artefact break in the measurement window holds the pace.
`setManualRate` (clamped) pauses adaptation; `resumeAuto` returns; a dizzy stop
(`reportDizziness`) is permanent.

A first implementation used a Robbins-Monro stochastic walk (move in a
direction while amplitude improves, reverse after two decreases). Simulation
showed it overshoots the amplitude peak and wanders down the far side, because
an improvement *confirms* the direction that is already past the peak. That
design was abandoned; probe-and-settle replaces it.

**Stability evidence** (`pacer/simulate.ts` + `pacer.test.ts`): three simulated
users (strongest oscillation at 5.0, 6.0, 6.75 breaths/min; amplitude
A(r) = 3 + 55·exp(−(r−r₀)²/(2·0.55²)) ms; continuous-phase HR sine + LCG noise),
20 simulated minutes, adaptation every 30 s. Asserted: every rate in bounds,
final-quarter rate within 0.5 of r₀, final-quarter total variation ≤ 0.6 with
≤3 direction changes. Trajectories converge by minute 4 and hold.

### 3. Web Bluetooth behind an interface, with honest fallbacks

`ble/heartRateDevice.ts` is the only file that knows Web Bluetooth exists.
Feature detection is `navigator.bluetooth` in a secure context — never
user-agent sniffing. `connect()` runs from a user gesture (the permission
explainer is shown before it), maps a dismissed chooser to
`UserCancelledError`, and the connection reconnects on
`gattserverdisconnected` with backoff (3 attempts: 1/2/4 s) and re-subscribes.
Status listeners replay the current state on subscribe. On browsers without
the API (Firefox, Safari today) the UI explains itself and offers the CSV
import; there is no disabled button pretending to work.

### 4. Everything local; only encrypted summaries persist

Raw RR intervals live in the session controller's memory and are dropped at
session end. A summary (pre/post RMSSD + mean HR + quality, mood ratings,
pace, stop reason, optional note) is saved **only on explicit request**, as an
AES-GCM-256 record (`store/summaryStore.ts`): non-extractable device CryptoKey
in IndexedDB, fresh 12-byte IV per record, AAD
`manovia:biofeedback:v1|summary=<id>` so records cannot be swapped. The threat
model is stated in the module header: it protects a storage dump or backup
from casual extraction; it does not protect against someone holding the
unlocked browser profile (the key is right there), and metadata (count, dates)
is visible. A passphrase-wrapped key is the honest upgrade and is parked.

### 5. The CSV path is the same pipeline, in the browser

`import/csvImport.ts` parses health-app exports (rr_ms / rr / rr_interval_s /
rr_1024 / hr-derived intervals, optional timestamps, `#` comment lines,
comma/semicolon/tab), refuses unrecognised files with the columns it found
named, and feeds the exact pipeline the sensor uses. No network call exists on
this path (the e2e blocks and records every `/api` request to prove it).

### 6. What this feature will not say

Session summaries are **associative and caveated**: pre/post observations with
their quality, the person's own mood words, and the fixed caveat that HRV
varies with sleep, caffeine, time of day, breathing, sensor fit and more.
No thresholds labelled good or bad, no diagnoses, no outcomes. The wording
guard (`ui/copy.test.ts`) enforces a denylist across en/hi/bn (with negation
awareness so the required "not a medical device" disclaimers are allowed) and
pins key/placeholder parity. Camera-pulse (the stretch goal) needs a quality
gate plus an honest accuracy statement and is **skipped to the Parking lot**.

### 7. Precache budget: hi/bn catalogues return to on-demand

Day 42's required trilingual copy (~28 KB across the three trees) pushed the
800 KB precache over budget. Rather than raise the budget, the hi/bn catalogue
chunks return to ADR 0025's on-demand design (they load when the language is
chosen; `i18n.ts` falls back to the bundled English tree if they are ever
unreachable). The biofeedback page itself stays precached — its pacer, CSV
import and summary work offline from a cold cache.

## Consequences

- Numbers are withheld often on noisy signal — deliberately; the copy explains
  why and the pacer keeps working regardless.
- The pacer converges within minutes and then holds; it will not chase quick
  changes. That is a feature (stability) with a cost (less "alive").
- A real chest strap cannot be exercised in this sandbox: the parsing and
  reconnect logic are covered by fakes and an injected browser stub; the radio
  itself is on the manual checklist.
- RMSSD values from sessions with many removals rest on fewer pairs than an
  interpolating implementation would use; the trade is documented above.
- Clearing site data destroys the device key and the saved summaries with it.
