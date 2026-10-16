/**
 * Session summary tests (Day 42): pre/post picking, withholding, trends, and
 * the copy contract — associations and caveats only, never health conclusions.
 */

import { describe, expect, it } from "vitest";

import {
  pickPrePost,
  summarizeSession,
  trendPoints,
  type SessionSummary,
} from "./summary";
import type { WindowMetrics } from "../signal/types";

function window(
  startMs: number,
  rmssdMs: number | null,
  quality: WindowMetrics["quality"] = "good",
): WindowMetrics {
  return {
    startMs,
    endMs: startMs + 60_000,
    acceptedBeats: 70,
    rejectedBeats: 1,
    gapMs: 0,
    quality,
    qualityScore: quality === "good" ? 0.98 : quality === "fair" ? 0.7 : 0.4,
    rmssdMs,
    meanHrBpm: rmssdMs === null ? null : 72,
  };
}

describe("pickPrePost", () => {
  it("takes the first and last window whose numbers were shown", () => {
    const { pre, post } = pickPrePost([
      window(0, null, "poor"),
      window(30_000, 42),
      window(60_000, 44),
      window(90_000, 46),
    ]);
    expect(pre?.rmssdMs).toBe(42);
    expect(post?.rmssdMs).toBe(46);
  });

  it("withholds both snapshots when nothing cleared the gate", () => {
    const { pre, post } = pickPrePost([window(0, null, "poor"), window(30_000, null, "poor")]);
    expect(pre).toBeNull();
    expect(post).toBeNull();
  });
});

describe("summarizeSession", () => {
  const input = {
    startedAtMs: Date.parse("2026-10-16T18:00:00Z"),
    endedAtMs: Date.parse("2026-10-16T18:10:00Z"),
    windows: [window(0, 40), window(300_000, 52)],
    preMood: 2 as const,
    postMood: 4 as const,
    paceRateStartBpm: 6,
    paceRateEndBpm: 5.5,
    autoAdjust: true,
    stopReason: "completed" as const,
    note: "felt calmer after",
  };

  it("records the session without drawing conclusions", () => {
    const summary = summarizeSession(input);
    expect(summary.format).toBe("manovia-biofeedback-summary/1");
    expect(summary.durationSec).toBe(600);
    expect(summary.pre?.rmssdMs).toBe(40);
    expect(summary.post?.rmssdMs).toBe(52);
    expect(summary.preMood).toBe(2);
    expect(summary.postMood).toBe(4);
    expect(summary.stopReason).toBe("completed");
    expect(summary.note).toBe("felt calmer after");
  });

  it("carries a dizzy stop through to the summary", () => {
    const summary = summarizeSession({ ...input, stopReason: "dizzy" });
    expect(summary.stopReason).toBe("dizzy");
  });
});

describe("trendPoints", () => {
  it("orders saved summaries oldest first and keeps nulls", () => {
    const older = summarizeSession({
      startedAtMs: Date.parse("2026-10-15T18:00:00Z"),
      endedAtMs: Date.parse("2026-10-15T18:05:00Z"),
      windows: [window(0, null, "poor")],
      preMood: 3,
      postMood: 3,
      paceRateStartBpm: 6,
      paceRateEndBpm: 6,
      autoAdjust: true,
      stopReason: "user",
    });
    const newer = summarizeSession({
      startedAtMs: Date.parse("2026-10-16T18:00:00Z"),
      endedAtMs: Date.parse("2026-10-16T18:05:00Z"),
      windows: [window(0, 41)],
      preMood: 3,
      postMood: 4,
      paceRateStartBpm: 6,
      paceRateEndBpm: 5.75,
      autoAdjust: true,
      stopReason: "completed",
    });
    const points = trendPoints([newer, older]);
    expect(points.map((p) => p.date)).toEqual([
      "2026-10-15T18:00:00.000Z",
      "2026-10-16T18:00:00.000Z",
    ]);
    expect(points[0]?.preRmssdMs).toBeNull();
    expect(points[1]?.preRmssdMs).toBe(41);
    expect(points[1]?.postMood).toBe(4);
  });
});

describe("summary copy contract", () => {
  /**
   * The strings a summary is allowed to be built from live in the locales
   * (`biofeedback.*`) and are checked by `ui/copy.test.ts` against the
   * forbidden-claims list. Here we pin the data-level contract: a summary
   * never gains fields that could carry a health conclusion.
   */
  it("has no diagnosis, risk, or health-conclusion fields", () => {
    const summary: SessionSummary = summarizeSession({
      startedAtMs: 0,
      endedAtMs: 1000,
      windows: [window(0, 40)],
      preMood: 3,
      postMood: 3,
      paceRateStartBpm: 6,
      paceRateEndBpm: 6,
      autoAdjust: true,
      stopReason: "completed",
    });
    const keys = Object.keys(summary).sort();
    expect(keys).toEqual([
      "autoAdjust",
      "durationSec",
      "endedAt",
      "format",
      "note",
      "paceRateEndBpm",
      "paceRateStartBpm",
      "post",
      "postMood",
      "pre",
      "preMood",
      "startedAt",
      "stopReason",
    ].sort());
  });
});
