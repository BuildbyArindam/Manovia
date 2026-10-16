/**
 * Heart-rate oscillation measurement for the breathing pacer (Day 42).
 *
 * "Resonance-style" biofeedback aims to pace breathing at the rate where the
 * person's heart-rate oscillation (the RSA wave: heart rate rising on the
 * inhale, falling on the exhale) is strongest. The measurement here is
 * deliberately simple and fully documented (ADR 0041 §Pacer):
 *
 * 1. take the trailing `windowMs` of *clean* RR intervals (the caller has
 *    already run artefact rejection and the quality gate);
 * 2. convert each interval to instantaneous heart rate (60000/RR) and
 *    resample onto a uniform 4 Hz grid by linear interpolation;
 * 3. subtract the mean, apply a Hann window, and evaluate a single-bin
 *    Goertzel at the frequency the user is currently breathing at
 *    (`rateBpm / 60` Hz);
 * 4. the returned amplitude (ms of heart-rate oscillation) is "how strongly
 *    the heart is swinging at this pace".
 *
 * This is a wellness pacing aid, not a clinical resonance-frequency
 * assessment (which would measure baroreflex gain under a full rate sweep).
 * The pacer treats amplitude-at-a-pace as an objective and climbs it slowly.
 */

import type { CleanSample } from "../signal/types";

/** Resampling grid for the Goertzel, Hz. */
export const RESAMPLE_HZ = 4;

export interface ResampledHr {
  /** Uniformly spaced heart-rate samples, bpm. */
  hr: Float64Array;
  /** Grid spacing, seconds. */
  stepSec: number;
  /** Timestamp of the first grid point, ms. */
  startMs: number;
}

/**
 * Resample instantaneous heart rate (60000/RR per beat, placed at the beat's
 * timestamp) onto a uniform grid covering `[nowMs - windowMs, nowMs]`.
 * Returns null when the window holds too few beats to interpolate.
 */
export function resampleHr(
  samples: ReadonlyArray<Pick<CleanSample, "rrMs" | "tMs">>,
  windowMs: number,
  nowMs: number,
  hz: number = RESAMPLE_HZ,
): ResampledHr | null {
  const inWindow = samples.filter((s) => s.tMs > nowMs - windowMs && s.tMs <= nowMs);
  if (inWindow.length < 4) {
    return null;
  }
  const stepSec = 1 / hz;
  // The grid covers the data actually present (at most `windowMs`): early in a
  // session there is less history, and flat-extending empty space would fake
  // oscillation that nobody's heart produced.
  const firstT = inWindow[0]?.tMs ?? nowMs - windowMs;
  const startMs = Math.max(nowMs - windowMs, firstT);
  const spanMs = nowMs - startMs;
  const n = Math.floor(spanMs / 1000 / stepSec) + 1;
  if (n < 4) {
    return null;
  }
  const hr = new Float64Array(n);
  let cursor = 0;
  for (let i = 0; i < n; i += 1) {
    const tMs = startMs + i * stepSec * 1000;
    // Advance to the bracketing pair. `inWindow` is sorted by tMs.
    while (
      cursor + 1 < inWindow.length &&
      (inWindow[cursor + 1]?.tMs ?? 0) < tMs
    ) {
      cursor += 1;
    }
    const left = inWindow[cursor];
    const right = inWindow[cursor + 1] ?? left;
    if (left === undefined || right === undefined) {
      return null;
    }
    const leftHr = 60_000 / left.rrMs;
    const rightHr = 60_000 / right.rrMs;
    if (right === left || right.tMs === left.tMs) {
      hr[i] = leftHr;
      continue;
    }
    const frac = Math.min(1, Math.max(0, (tMs - left.tMs) / (right.tMs - left.tMs)));
    hr[i] = leftHr + (rightHr - leftHr) * frac;
  }
  return { hr, stepSec, startMs };
}

/**
 * Hann-windowed single-bin Goertzel amplitude (bpm) at `rateBpm`.
 *
 * A = 2 * |sum(w[n] * x[n] * e^{-j 2 pi f n / hz})| / sum(w[n])
 *
 * with x the mean-removed series and w the Hann window, so a pure sinusoid of
 * amplitude A at f returns ~A even when the window is not a whole number of
 * cycles.
 */
export function oscillationAmplitude(
  resampled: ResampledHr,
  rateBpm: number,
): number {
  const { hr, stepSec } = resampled;
  const n = hr.length;
  if (n < 4) {
    return 0;
  }
  let mean = 0;
  for (let i = 0; i < n; i += 1) {
    mean += hr[i] ?? 0;
  }
  mean /= n;

  const omega = (2 * Math.PI * rateBpm) / 60 / (1 / stepSec); // radians per sample
  let re = 0;
  let im = 0;
  let wSum = 0;
  for (let i = 0; i < n; i += 1) {
    const w = 0.5 * (1 - Math.cos((2 * Math.PI * i) / (n - 1)));
    const x = w * ((hr[i] ?? 0) - mean);
    re += x * Math.cos(omega * i);
    im -= x * Math.sin(omega * i);
    wSum += w;
  }
  return (2 * Math.hypot(re, im)) / wSum;
}

/**
 * Measure how strongly the heart is oscillating at `rateBpm` over the trailing
 * window of clean samples. Null when there is not enough data.
 */
export function measureOscillation(
  samples: ReadonlyArray<Pick<CleanSample, "rrMs" | "tMs">>,
  windowMs: number,
  nowMs: number,
  rateBpm: number,
  hz: number = RESAMPLE_HZ,
): number | null {
  const resampled = resampleHr(samples, windowMs, nowMs, hz);
  if (resampled === null) {
    return null;
  }
  return oscillationAmplitude(resampled, rateBpm);
}
