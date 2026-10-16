/**
 * Shared types for the Day 42 biofeedback signal pipeline.
 *
 * The pipeline is pure TypeScript with no dependencies so every stage is
 * unit-testable against recorded fixtures and an independent NumPy reference
 * (`scripts/day42_hrv_reference.py`). Nothing here talks to a device, a
 * browser API or the network.
 *
 * WELLNESS FEATURE. These numbers are for a breathing biofeedback pacer and a
 * personal trend view. They are not a medical measurement, not a diagnosis and
 * not a monitor of any condition (see docs/adr/0041-biofeedback-hrv.md).
 */

/** One beat-to-beat (RR) interval from a heart-rate sensor or a CSV import. */
export interface RRSample {
  /** RR interval duration in milliseconds (R-peak to R-peak). */
  rrMs: number;
  /**
   * Timestamp of the beat that *ends* this interval, in milliseconds on one
   * continuous clock. Successive intervals share a beat, so for an
   * uninterrupted series `tMs[i + 1] - tMs[i] === samples[i + 1].rrMs`.
   */
  tMs: number;
  /**
   * Set when the record has a discontinuity *before* this interval: sensor
   * packet loss, a gap in a CSV export, or anything else that means the
   * previous interval is not the beat this one follows.
   */
  breakBefore?: boolean;
}

/** Why one beat was removed by artefact rejection. */
export type ArtefactReason = "out-of-range" | "ectopic";

/** An accepted interval, annotated for the metric stage. */
export interface CleanSample extends RRSample {
  /** Index in the input series, so removals can be audited. */
  rawIndex: number;
  /**
   * True when this interval does not directly continue the previously accepted
   * one (an interval was removed in between, or the timeline has a gap).
   * Successive-difference metrics (RMSSD) must skip the pair across a break.
   */
  breakBefore: boolean;
}

export interface RejectedBeat {
  rawIndex: number;
  rrMs: number;
  /** Timestamp of the beat that ends this interval (same clock as samples). */
  tMs: number;
  reason: ArtefactReason;
}

/** A discontinuity in the beat timeline (dropout / packet loss). */
export interface TimelineGap {
  /** Gap sits between `afterRawIndex` and `afterRawIndex + 1`. */
  afterRawIndex: number;
  /** Timestamps either side of the gap (same clock as samples). */
  tBeforeMs: number;
  tAfterMs: number;
  /** Milliseconds of wall-clock time missing from the beat record. */
  gapMs: number;
  /** `gapMs / median RR`, minus the beats that are present. Never negative. */
  estimatedMissingBeats: number;
}

export interface CleanSeries {
  accepted: CleanSample[];
  rejected: RejectedBeat[];
  gaps: TimelineGap[];
  /** Total intervals that went in (accepted + rejected). */
  total: number;
}

/**
 * Signal quality for a window. "poor" means the UI must withhold the numbers:
 * a confidently wrong RMSSD is worse than none.
 */
export type QualityLevel = "good" | "fair" | "poor";

export interface WindowMetrics {
  startMs: number;
  endMs: number;
  acceptedBeats: number;
  rejectedBeats: number;
  /** Total gap time inside the window, ms. */
  gapMs: number;
  quality: QualityLevel;
  /** Continuous 0..1 score behind the level, for display and tests. */
  qualityScore: number;
  /**
   * RMSSD over the window, ms. **Null when quality is poor** — the refusal is
   * in the data model, so no surface can "forget" to enforce it.
   */
  rmssdMs: number | null;
  /** Mean heart rate over the window, bpm. Null when quality is poor. */
  meanHrBpm: number | null;
}
