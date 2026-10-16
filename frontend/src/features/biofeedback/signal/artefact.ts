/**
 * Artefact rejection for RR interval series (Day 42).
 *
 * Wrist and chest sensors mis-detect beats: ectopic beats (a real extra or
 * missed beat), motion artefacts, and dropouts. Computing RMSSD over those
 * produces large, confident, wrong numbers — the worst possible output for a
 * wellness feature. So the pipeline removes what it cannot trust *before*
 * any metric is computed, and reports what it removed.
 *
 * The algorithm (documented in docs/adr/0041-biofeedback-hrv.md §Signal):
 *
 * 1. **Range filter.** RR outside [300, 2000] ms (i.e. <30 or >200 bpm) is
 *    physiologically implausible for this feature and is removed as
 *    `out-of-range`.
 * 2. **Local median (ectopic) filter.** Each remaining interval is compared to
 *    the median of its ±2 neighbours in the raw series. A deviation larger
 *    than `ectopicRelThreshold` (default 20%, in the spirit of the widely used
 *    "local-median" filters in HRV tooling) is removed as `ectopic`.
 * 3. **Gap marking.** Where the beat timeline has a discontinuity — `breakBefore`
 *    flags from the stream, or timestamp spacing that cannot match the next
 *    interval length — the gap is recorded and the following interval starts a
 *    fresh successive-difference chain. Gap detection is how a dropout stops
 *    being two unrelated windows pretending to be neighbours.
 *
 * Rejection is deliberately conservative and always auditable: every removed
 * beat carries its index, value and reason.
 *
 * WELLNESS FEATURE. See docs/adr/0041-biofeedback-hrv.md — not a medical device.
 */

import type {
  CleanSample,
  CleanSeries,
  RejectedBeat,
  RRSample,
  TimelineGap,
} from "./types";

export interface ArtefactOptions {
  /** Physiological plausibility floor, ms (default 300 => 200 bpm). */
  minRrMs: number;
  /** Physiological plausibility ceiling, ms (default 2000 => 30 bpm). */
  maxRrMs: number;
  /** Relative deviation from the local median that marks an ectopic beat. */
  ectopicRelThreshold: number;
  /** Neighbours on each side in the local-median window (default 2 => 5 taps). */
  medianRadius: number;
  /**
   * A timestamp gap larger than `gapFactor * max(prev.rr, next.rr)` marks
   * missing beats between two samples.
   */
  gapFactor: number;
}

export const DEFAULT_ARTEFACT_OPTIONS: ArtefactOptions = {
  minRrMs: 300,
  maxRrMs: 2000,
  ectopicRelThreshold: 0.2,
  medianRadius: 2,
  gapFactor: 1.75,
};

/** Median of a non-empty numeric window (no sorting of the caller's array). */
function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  if (sorted.length % 2 === 1) {
    return sorted[mid] ?? Number.NaN;
  }
  const a = sorted[mid - 1] ?? Number.NaN;
  const b = sorted[mid] ?? Number.NaN;
  return (a + b) / 2;
}

/**
 * Find timeline discontinuities: places where `tMs` spacing cannot be the
 * continuation of the beat sequence. Returns the gap list and a set of raw
 * indices whose sample begins after a gap.
 */
export function detectGaps(
  samples: readonly RRSample[],
  options: Partial<ArtefactOptions> = {},
): {
  gaps: TimelineGap[];
  breakAfter: Set<number>;
} {
  const opts = { ...DEFAULT_ARTEFACT_OPTIONS, ...options };
  const gaps: TimelineGap[] = [];
  const breakAfter = new Set<number>();
  const rrValues = samples.map((s) => s.rrMs);
  const medianRr = rrValues.length > 0 ? median(rrValues) : 0;
  for (let i = 0; i + 1 < samples.length; i += 1) {
    const current = samples[i];
    const next = samples[i + 1];
    if (current === undefined || next === undefined) {
      continue;
    }
    const spacing = next.tMs - current.tMs;
    const limit = next.rrMs * opts.gapFactor;
    const explicit = next.breakBefore === true;
    // +250 ms of slack absorbs timestamp quantisation in CSV exports.
    if (explicit || spacing > limit + 250) {
      const gapMs = Math.max(0, spacing - next.rrMs);
      const estimatedMissingBeats =
        medianRr > 0 ? Math.max(0, Math.round(gapMs / medianRr) - 1) : 0;
      gaps.push({
        afterRawIndex: i,
        tBeforeMs: current.tMs,
        tAfterMs: next.tMs,
        gapMs,
        estimatedMissingBeats,
      });
      breakAfter.add(i + 1);
    }
  }
  return { gaps, breakAfter };
}

/**
 * Run the artefact pipeline over a raw series and return the clean series
 * with removals, gaps and break flags annotated.
 */
export function rejectArtefacts(
  samples: readonly RRSample[],
  options: Partial<ArtefactOptions> = {},
): CleanSeries {
  const opts = { ...DEFAULT_ARTEFACT_OPTIONS, ...options };
  const { gaps, breakAfter } = detectGaps(samples, opts);

  // Pass 1: range filter, over the raw series.
  const inRange: boolean[] = samples.map(
    (s) => s.rrMs >= opts.minRrMs && s.rrMs <= opts.maxRrMs,
  );

  // Pass 2: local-median (ectopic) filter. The window is over raw indices, so
  // a rejected neighbour still anchors the local median the way an artefact
  // sits in a real recording.
  const rejected: RejectedBeat[] = samples.map((s, i) => ({
    rawIndex: i,
    rrMs: s.rrMs,
    tMs: s.tMs,
    reason: "ectopic" as const,
  }));
  const isRejected: boolean[] = samples.map((s, i) => {
    if (!inRange[i]) {
      rejected[i] = { rawIndex: i, rrMs: s.rrMs, tMs: s.tMs, reason: "out-of-range" };
      return true;
    }
    const window: number[] = [];
    for (let k = i - opts.medianRadius; k <= i + opts.medianRadius; k += 1) {
      const value = samples[k]?.rrMs;
      if (value !== undefined && inRange[k] === true) {
        window.push(value);
      }
    }
    const localMedian = median(window.length > 0 ? window : [s.rrMs]);
    const deviation = Math.abs(s.rrMs - localMedian) / localMedian;
    if (deviation > opts.ectopicRelThreshold) {
      rejected[i] = { rawIndex: i, rrMs: s.rrMs, tMs: s.tMs, reason: "ectopic" };
      return true;
    }
    return false;
  });

  const accepted: CleanSample[] = [];
  const removed: RejectedBeat[] = [];
  let previousAcceptedRawIndex: number | null = null;
  samples.forEach((s, i) => {
    if (isRejected[i] === true) {
      const entry = rejected[i];
      if (entry !== undefined) {
        removed.push(entry);
      }
      return;
    }
    // A fresh successive-difference chain starts after any removal or gap.
    const breakBefore =
      previousAcceptedRawIndex !== null &&
      (i !== previousAcceptedRawIndex + 1 || breakAfter.has(i));
    accepted.push({
      rrMs: s.rrMs,
      tMs: s.tMs,
      rawIndex: i,
      breakBefore,
    });
    previousAcceptedRawIndex = i;
  });

  return { accepted, rejected: removed, gaps, total: samples.length };
}
