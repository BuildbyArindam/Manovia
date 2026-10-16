/**
 * Session summary for biofeedback breathing (Day 42).
 *
 * A summary is deliberately associative and caveated (AGENTS.md: never
 * diagnose, never promise outcomes, no thresholds labelled good/bad):
 *
 *   - pre/post RMSSD and mean HR are *observations from this recording*, with
 *     the quality they were computed under, and null when quality withheld
 *     them;
 *   - mood is the person's own 1-5 rating, not a clinical instrument;
 *   - the trend view joins saved summaries so somebody can see how their
 *     numbers moved **across their own sessions** — described as association,
 *     with the caveats string, never as an outcome or a health conclusion.
 *
 * Raw RR data is never persisted: the summary carries metrics and context
 * only, and is stored encrypted (see `store/summaryStore.ts`) after an
 * explicit save.
 *
 * WELLNESS FEATURE. Not a medical device. HRV varies with sleep, caffeine,
 * time of day, how you breathed, how the sensor sat, and much else.
 */

import type { QualityLevel, WindowMetrics } from "../signal/types";

/** Mood rating: the person's own words, 1..5. */
export type MoodRating = 1 | 2 | 3 | 4 | 5;

export interface SessionSnapshot {
  /** Null when quality was poor — the summary then says so, it does not guess. */
  rmssdMs: number | null;
  meanHrBpm: number | null;
  quality: QualityLevel;
  qualityScore: number;
  windowStartMs: number;
  windowEndMs: number;
}

export type SessionStopReason = "completed" | "dizzy" | "user";

export interface SessionSummary {
  /** Versioned payload shape; the encrypted store records this too. */
  format: "manovia-biofeedback-summary/1";
  startedAt: string;
  endedAt: string;
  durationSec: number;
  preMood: MoodRating | null;
  postMood: MoodRating | null;
  pre: SessionSnapshot | null;
  post: SessionSnapshot | null;
  paceRateStartBpm: number;
  paceRateEndBpm: number;
  autoAdjust: boolean;
  stopReason: SessionStopReason;
  /** Free-text note the person may add; stored encrypted with the rest. */
  note: string | null;
}

export function snapshotFromWindow(window: WindowMetrics): SessionSnapshot {
  return {
    rmssdMs: window.rmssdMs,
    meanHrBpm: window.meanHrBpm,
    quality: window.quality,
    qualityScore: window.qualityScore,
    windowStartMs: window.startMs,
    windowEndMs: window.endMs,
  };
}

/**
 * Pre is the first window where the numbers were shown (quality good/fair and
 * metrics present); post is the last such window. When nothing cleared the
 * gate, the snapshot is null and the summary withholds the numbers — same
 * rule as the live view.
 */
export function pickPrePost(windows: readonly WindowMetrics[]): {
  pre: SessionSnapshot | null;
  post: SessionSnapshot | null;
} {
  const shown = windows.filter(
    (w) => w.quality !== "poor" && w.rmssdMs !== null && w.meanHrBpm !== null,
  );
  const first = shown[0];
  const last = shown[shown.length - 1];
  return {
    pre: first !== undefined ? snapshotFromWindow(first) : null,
    post: last !== undefined ? snapshotFromWindow(last) : null,
  };
}

export interface SummarizeInput {
  startedAtMs: number;
  endedAtMs: number;
  windows: readonly WindowMetrics[];
  preMood: MoodRating | null;
  postMood: MoodRating | null;
  paceRateStartBpm: number;
  paceRateEndBpm: number;
  autoAdjust: boolean;
  stopReason: SessionStopReason;
  note?: string | null;
}

export function summarizeSession(input: SummarizeInput): SessionSummary {
  const { pre, post } = pickPrePost(input.windows);
  return {
    format: "manovia-biofeedback-summary/1",
    startedAt: new Date(input.startedAtMs).toISOString(),
    endedAt: new Date(input.endedAtMs).toISOString(),
    durationSec: Math.max(0, Math.round((input.endedAtMs - input.startedAtMs) / 1000)),
    preMood: input.preMood,
    postMood: input.postMood,
    pre,
    post,
    paceRateStartBpm: input.paceRateStartBpm,
    paceRateEndBpm: input.paceRateEndBpm,
    autoAdjust: input.autoAdjust,
    stopReason: input.stopReason,
    note: input.note ?? null,
  };
}

export interface TrendPoint {
  date: string;
  preRmssdMs: number | null;
  postRmssdMs: number | null;
  preMood: MoodRating | null;
  postMood: MoodRating | null;
  durationSec: number;
}

/**
 * One point per saved summary, oldest first. Nulls are preserved: a session
 * whose numbers were withheld still exists in the person's history.
 */
export function trendPoints(summaries: readonly SessionSummary[]): TrendPoint[] {
  return [...summaries]
    .sort((a, b) => a.startedAt.localeCompare(b.startedAt))
    .map((s) => ({
      date: s.startedAt,
      preRmssdMs: s.pre?.rmssdMs ?? null,
      postRmssdMs: s.post?.rmssdMs ?? null,
      preMood: s.preMood,
      postMood: s.postMood,
      durationSec: s.durationSec,
    }));
}

/**
 * The fixed caveat carried with every summary and trend. Kept as data (not
 * formatted copy) so the wording guard test can check it and the UI can
 * translate it; see `biofeedback.summary.caveats` in the locales.
 */
export const SUMMARY_CAVEATS_VERSION = 1;
