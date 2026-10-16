/**
 * HRV metrics over RR interval series (Day 42).
 *
 * Two numbers only — mean heart rate and RMSSD — because those are the two the
 * pacer and the session summary need, and every extra number is another chance
 * to look like a medical instrument.
 *
 * **RMSSD** (root mean square of successive differences): for each pair of
 * intervals that share a beat, the squared difference of their lengths;
 * RMSSD is the root of the mean of those squares. Pairs that cross an artefact
 * removal or a timeline gap are *skipped*, not interpolated: an interpolated
 * difference invents the very quantity being measured. (Interpolation-based
 * correction is common in clinical HRV tooling; for a wellness pacer the
 * honest move is to need clean beats and say so. Documented in ADR 0041.)
 *
 * **Sliding windows** (default 60 s long, stepping 30 s) compute both metrics
 * per window and carry the quality level. When quality is `poor` the metric
 * fields are **null** — the refusal to display lives in the data model, so a
 * future surface cannot leak a number the gate would have hidden.
 *
 * WELLNESS FEATURE. Not a medical device, not a diagnosis, not a monitor.
 */

import { assessQuality } from "./quality";
import type { CleanSample, CleanSeries, WindowMetrics } from "./types";

export interface WindowOptions {
  /** Window length, ms. */
  windowMs: number;
  /** Step between consecutive window starts, ms. */
  stepMs: number;
  /** Minimum accepted intervals in a window before anything is computed. */
  minBeats: number;
  /** Minimum successive pairs (non-break) needed for an RMSSD. */
  minSuccessivePairs: number;
  /** Sensor contact state; null when the sensor does not report it. */
  contactDetected: boolean | null;
}

export const DEFAULT_WINDOW_OPTIONS: WindowOptions = {
  windowMs: 60_000,
  stepMs: 30_000,
  minBeats: 15,
  minSuccessivePairs: 10,
  contactDetected: null,
};

/**
 * RMSSD in ms over an already-cleaned series, or null when there are too few
 * successive pairs to compute one. Pairs across `breakBefore` are skipped.
 */
export function rmssdMs(
  samples: ReadonlyArray<Pick<CleanSample, "rrMs" | "breakBefore">>,
  minSuccessivePairs = 10,
): number | null {
  const squares: number[] = [];
  for (let i = 0; i + 1 < samples.length; i += 1) {
    const next = samples[i + 1];
    const current = samples[i];
    if (current === undefined || next === undefined || next.breakBefore) {
      continue;
    }
    const delta = next.rrMs - current.rrMs;
    squares.push(delta * delta);
  }
  if (squares.length < minSuccessivePairs) {
    return null;
  }
  const sum = squares.reduce((acc, value) => acc + value, 0);
  return Math.sqrt(sum / squares.length);
}

/** Mean heart rate in bpm over a cleaned series (60000 / mean RR). */
export function meanHeartRateBpm(samples: ReadonlyArray<Pick<CleanSample, "rrMs">>): number | null {
  if (samples.length === 0) {
    return null;
  }
  const sum = samples.reduce((acc, s) => acc + s.rrMs, 0);
  return 60_000 / (sum / samples.length);
}

/**
 * Fraction of successive accepted pairs whose RR values differ by less than
 * 1 ms. A sensor stuck on one value would otherwise produce a confidently
 * tiny RMSSD; `quality.ts` caps the score instead.
 */
export function flatlineFraction(
  samples: ReadonlyArray<Pick<CleanSample, "rrMs" | "breakBefore">>,
): number {
  let pairs = 0;
  let flat = 0;
  for (let i = 0; i + 1 < samples.length; i += 1) {
    const next = samples[i + 1];
    const current = samples[i];
    if (current === undefined || next === undefined || next.breakBefore) {
      continue;
    }
    pairs += 1;
    if (Math.abs(next.rrMs - current.rrMs) < 1) {
      flat += 1;
    }
  }
  return pairs === 0 ? 0 : flat / pairs;
}

/**
 * Sliding-window metrics over a cleaned series.
 *
 * Windows are half-open on the left `(start, end]`, stepping `stepMs` from
 * `firstT - windowMs`. A window with no accepted samples is skipped. Gap time
 * is attributed to the window containing the break (`tAfterMs`).
 */
export function slidingWindowMetrics(
  series: CleanSeries,
  options: Partial<WindowOptions> = {},
): WindowMetrics[] {
  const opts = { ...DEFAULT_WINDOW_OPTIONS, ...options };
  const { accepted, rejected, gaps, total } = series;
  if (total === 0 || accepted.length === 0) {
    return [];
  }
  const firstT = accepted[0]?.tMs ?? 0;
  const lastT = accepted[accepted.length - 1]?.tMs ?? firstT;
  const start0 = firstT - opts.windowMs;
  const out: WindowMetrics[] = [];

  for (let start = start0; start < lastT; start += opts.stepMs) {
    const end = start + opts.windowMs;
    const inWindow = accepted.filter((s) => s.tMs > start && s.tMs <= end);
    if (inWindow.length === 0) {
      continue;
    }
    const rejectedInWindow = rejected.filter((r) => r.tMs > start && r.tMs <= end).length;
    const gapMs = gaps
      .filter((g) => g.tAfterMs > start && g.tAfterMs <= end)
      .reduce((acc, g) => acc + g.gapMs, 0);

    const quality = assessQuality({
      acceptedBeats: inWindow.length,
      rejectedBeats: rejectedInWindow,
      gapMs,
      windowMs: opts.windowMs,
      contactDetected: opts.contactDetected,
      minBeats: opts.minBeats,
      flatlineFraction: flatlineFraction(inWindow),
    });

    const withheld = quality.level === "poor";
    const rmssd = rmssdMs(inWindow, opts.minSuccessivePairs);
    const meanHr = meanHeartRateBpm(inWindow);

    out.push({
      startMs: start,
      endMs: end,
      acceptedBeats: inWindow.length,
      rejectedBeats: rejectedInWindow,
      gapMs,
      quality: quality.level,
      qualityScore: quality.score,
      rmssdMs: withheld ? null : rmssd,
      meanHrBpm: withheld ? null : meanHr,
    });
  }
  return out;
}
