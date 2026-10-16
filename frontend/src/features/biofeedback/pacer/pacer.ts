/**
 * Adaptive resonance-style breathing pacer (Day 42).
 *
 * The pacer shows a breathing cue (inhale/exhale at equal length by default)
 * and **slowly adjusts the pace toward the rate where the user's heart-rate
 * oscillation is strongest**, within a bounded range, always user-overridable,
 * and with a permanent "I feel dizzy — stop" escape.
 *
 * ## The algorithm (ADR 0041 §Pacer)
 *
 * Measurement. Every `adaptIntervalMs` (30 s) while a session runs, the
 * pacer scores the heart-rate oscillation *at the current pace* with
 * {@link measureOscillation} over the data gathered since the pace last
 * changed (capped at `measurementWindowMs`, 120 s) — the amplitude of the
 * heart-rate swing at the rate the user is breathing right now. Measuring the
 * stretch of one pace (not a window smeared across pace changes) is what makes
 * amplitudes comparable.
 *
 * Movement. A deterministic probe-and-settle walk:
 *
 *   1. **Fixed probes.** First the pacer walks (at most one `maxStepBpm` per
 *      adaptation) to `start + 0.5`, then to `start - 0.5`, measuring at
 *      every step along the way. Two probes are enough to learn which side of
 *      the start the strong oscillation is on.
 *   2. **Neighbour fills.** Then it probes 0.25 bpm neighbours of the best
 *      rate seen so far, alternating sides, skipping rates already measured.
 *      When both neighbours of the best are measured and neither beats it,
 *      probing is done.
 *   3. **Settle.** The pace walks one step per adaptation toward the
 *      best-measured rate and then holds there. It does not hunt: a settled
 *      pacer stays put unless probing found something better.
 *
 * Every move is at most `maxStepBpm` (0.25 breaths/min per 30 s), so the pace
 * never jumps; the range is hard-clamped to [4.5, 7.0]; the sequence is fully
 * deterministic given the measurements, so `pacer.test.ts` pins the exact
 * trajectory for three simulated users.
 *
 * ## Boundaries
 *
 * - Range: [4.5, 7.0] breaths/min — slow, calm pacing; never outside.
 * - Quality gate: no adaptation while signal quality is poor or the measured
 *   window contains an artefact break (the amplitude would be fiction).
 * - Manual override: `setManualRate` freezes adaptation and hands the pace to
 *   the user; `resumeAuto` hands it back. The current pace is never hidden.
 * - Dizziness: `reportDizziness()` stops the session immediately and
 *   permanently (`stopReason: "dizzy"`), and the UI keeps that button on
 *   screen for the whole session.
 *
 * WELLNESS FEATURE. Not a medical device. If breathing slowly ever feels
 * wrong, stop — that is what the button is for.
 */

import type { CleanSample } from "../signal/types";
import { measureOscillation } from "./hrOscillation";

export interface PacerConfig {
  /** Pace the pacer starts and idles at, breaths/min. */
  defaultRateBpm: number;
  minRateBpm: number;
  maxRateBpm: number;
  /** How often adaptation may consider moving, ms. */
  adaptIntervalMs: number;
  /** Largest pace change per adaptation, breaths/min. */
  maxStepBpm: number;
  /** Cap on the measurement window (the window is "since last move"). */
  measurementWindowMs: number;
  /** Fixed probe offsets from the start pace, breaths/min. */
  probeOffsetsBpm: readonly number[];
  /** Spacing of the neighbour-fill probes around the best rate, bpm. */
  fillStepBpm: number;
  /** Two rates within this distance count as the same rate. */
  rateEpsilonBpm: number;
  /** A measurement window containing an artefact break is not measured. */
  requireCleanWindow: boolean;
}

export const DEFAULT_PACER_CONFIG: PacerConfig = {
  defaultRateBpm: 6.0,
  minRateBpm: 4.5,
  maxRateBpm: 7.0,
  adaptIntervalMs: 30_000,
  maxStepBpm: 0.25,
  measurementWindowMs: 120_000,
  probeOffsetsBpm: [0.5, -0.5],
  fillStepBpm: 0.25,
  rateEpsilonBpm: 0.05,
  requireCleanWindow: true,
};

export type PacerPhase = "inhale" | "exhale";

export type StopReason = "dizzy" | "user";

export interface PacerState {
  /** Current pace, breaths/min. Always in [minRateBpm, maxRateBpm]. */
  rateBpm: number;
  phase: PacerPhase;
  /** 0..1 progress within the current phase, for the cue orb. */
  phaseProgress: number;
  cycleSeconds: number;
  autoAdjust: boolean;
  stopped: boolean;
  stopReason: StopReason | null;
}

export type AdaptationDecision =
  | "moved"
  | "held-peak"
  | "held-boundary"
  | "held-quality"
  | "held-data"
  | "held-no-measurement"
  | "held-manual"
  | "held-stopped"
  | "held-not-due";

export interface AdaptationEvent {
  decision: AdaptationDecision;
  fromBpm: number;
  toBpm: number;
  /** Measured oscillation amplitude at `fromBpm` (ms), when measured. */
  amplitudeMs: number | null;
  atMs: number;
}

/**
 * The pacer. Pure state machine over explicit time and measurements — no
 * timers, no DOM, no device access — so every behaviour is unit-testable.
 */
export class BreathingPacer {
  private readonly config: PacerConfig;
  private rateBpm: number;
  private phaseDistance = 0; // in breaths (1.0 == one full cycle)
  private autoAdjust = true;
  private stopped = false;
  private stopReason: StopReason | null = null;

  // Adaptation state (probe-and-settle).
  private lastAdaptMs = -Infinity;
  private lastMoveMs = -Infinity;
  private bestRateBpm: number;
  private bestAmplitudeMs = -Infinity;
  private visitedRates: number[] = [];
  private fixedProbeIndex = 0;
  private fillSide: 1 | -1 = -1;
  private mode: "probe" | "settle" = "probe";

  constructor(config: Partial<PacerConfig> = {}) {
    this.config = { ...DEFAULT_PACER_CONFIG, ...config };
    this.rateBpm = clamp(
      this.config.defaultRateBpm,
      this.config.minRateBpm,
      this.config.maxRateBpm,
    );
    this.bestRateBpm = this.rateBpm;
  }

  /** Advance time by `dtMs` and return the current cue state. */
  advance(dtMs: number): PacerState {
    if (!this.stopped) {
      this.phaseDistance += dtMs / (60_000 / this.rateBpm);
    }
    return this.state();
  }

  /** Current cue state without advancing time. */
  state(): PacerState {
    const cycleInBreath = this.phaseDistance % 1;
    const inhale = cycleInBreath < 0.5;
    return {
      rateBpm: this.rateBpm,
      phase: inhale ? "inhale" : "exhale",
      phaseProgress: inhale ? cycleInBreath * 2 : (cycleInBreath - 0.5) * 2,
      cycleSeconds: 60 / this.rateBpm,
      autoAdjust: this.autoAdjust,
      stopped: this.stopped,
      stopReason: this.stopReason,
    };
  }

  /**
   * Manual override: the user picks the pace. Adaptation pauses until
   * {@link resumeAuto} is called. The value is clamped to the bounds.
   */
  setManualRate(rateBpm: number): void {
    this.rateBpm = clamp(rateBpm, this.config.minRateBpm, this.config.maxRateBpm);
    this.autoAdjust = false;
  }

  /** Hand the pace back to the adaptive algorithm. */
  resumeAuto(): void {
    if (!this.stopped) {
      this.autoAdjust = true;
    }
  }

  /** The "I feel dizzy" button. Stops immediately and permanently. */
  reportDizziness(): void {
    this.stop("dizzy");
  }

  /** Stop the session (end button, or dizziness). */
  stop(reason: StopReason): void {
    this.stopped = true;
    this.stopReason = reason;
    this.autoAdjust = false;
  }

  /** The exact bounds in force (for UI display and tests). */
  get bounds(): { min: number; max: number; step: number } {
    return {
      min: this.config.minRateBpm,
      max: this.config.maxRateBpm,
      step: this.config.maxStepBpm,
    };
  }

  /** Where the algorithm believes the strongest oscillation is. */
  get bestRate(): number {
    return this.bestRateBpm;
  }

  /** "probe" while still exploring, "settle" once the best is known. */
  get phase2(): "probe" | "settle" {
    return this.mode;
  }

  /**
   * Consider moving the pace. Call this every `adaptIntervalMs` with the
   * clean series so far and its quality. Returns what happened; the caller
   * shows it and the tests pin it.
   */
  adapt(
    samples: ReadonlyArray<CleanSample>,
    qualityLevel: "good" | "fair" | "poor",
    nowMs: number,
  ): AdaptationEvent {
    const from = this.rateBpm;
    const held = (decision: AdaptationDecision, amplitudeMs: number | null): AdaptationEvent => ({
      decision,
      fromBpm: from,
      toBpm: from,
      amplitudeMs,
      atMs: nowMs,
    });

    if (this.stopped) {
      return held("held-stopped", null);
    }
    if (!this.autoAdjust) {
      return held("held-manual", null);
    }
    if (nowMs - this.lastAdaptMs < this.config.adaptIntervalMs) {
      return held("held-not-due", null);
    }
    this.lastAdaptMs = nowMs;

    if (qualityLevel === "poor") {
      return held("held-quality", null);
    }

    // Measure the stretch of the current pace (capped), so amplitudes at
    // different rates are compared like for like.
    const windowStartMs = Math.max(this.lastMoveMs, nowMs - this.config.measurementWindowMs);
    const window = samples.filter((s) => s.tMs > windowStartMs && s.tMs <= nowMs);
    if (this.config.requireCleanWindow !== false && window.some((s) => s.breakBefore)) {
      return held("held-data", null);
    }
    const amplitudeMs = measureOscillation(
      window.map((s) => ({ rrMs: s.rrMs, tMs: s.tMs })),
      nowMs - windowStartMs,
      nowMs,
      this.rateBpm,
    );
    if (amplitudeMs === null) {
      return held("held-no-measurement", null);
    }

    if (amplitudeMs > this.bestAmplitudeMs) {
      this.bestAmplitudeMs = amplitudeMs;
      this.bestRateBpm = this.rateBpm;
    }
    if (!this.visitedRates.some((r) => Math.abs(r - this.rateBpm) < this.config.rateEpsilonBpm)) {
      this.visitedRates.push(this.rateBpm);
    }

    const target = this.nextTarget();
    const distance = target - this.rateBpm;
    if (Math.abs(distance) < this.config.rateEpsilonBpm) {
      if (this.mode === "settle") {
        return held("held-peak", amplitudeMs);
      }
      // Sitting on the probe target with nowhere new to go: settle.
      this.mode = "settle";
      return held("held-peak", amplitudeMs);
    }
    const step = Math.sign(distance) * Math.min(this.config.maxStepBpm, Math.abs(distance));
    const next = clamp(this.rateBpm + step, this.config.minRateBpm, this.config.maxRateBpm);
    if (next === this.rateBpm) {
      return held("held-boundary", amplitudeMs);
    }
    this.rateBpm = next;
    this.lastMoveMs = nowMs;
    return { decision: "moved", fromBpm: from, toBpm: next, amplitudeMs, atMs: nowMs };
  }

  /** The next point the walk is heading toward (probe or settle target). */
  private nextTarget(): number {
    const { minRateBpm, maxRateBpm, probeOffsetsBpm, fillStepBpm, rateEpsilonBpm } = this.config;

    while (this.fixedProbeIndex < probeOffsetsBpm.length) {
      const offset = probeOffsetsBpm[this.fixedProbeIndex] ?? 0;
      this.fixedProbeIndex += 1;
      const target = clamp(this.config.defaultRateBpm + offset, minRateBpm, maxRateBpm);
      if (
        this.visitedRates.some((r) => Math.abs(r - target) < rateEpsilonBpm) ||
        Math.abs(target - this.rateBpm) < rateEpsilonBpm
      ) {
        continue; // already measured here; try the next fixed probe
      }
      return target;
    }

    for (const side of [this.fillSide, -this.fillSide] as const) {
      const candidate = clamp(this.bestRateBpm + side * fillStepBpm, minRateBpm, maxRateBpm);
      if (
        !this.visitedRates.some((r) => Math.abs(r - candidate) < rateEpsilonBpm) &&
        Math.abs(candidate - this.bestRateBpm) >= rateEpsilonBpm
      ) {
        this.fillSide = side === 1 ? -1 : 1;
        return candidate;
      }
    }

    this.mode = "settle";
    return this.bestRateBpm;
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
