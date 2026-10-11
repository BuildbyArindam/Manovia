/**
 * The row-shaping layer, which is where "the chart and the table show the same
 * numbers" is either true or not.
 *
 * These tests use the same fixtures the page tests use, so a change in what the
 * server sends shows up in both places rather than in one. The interesting cases
 * are the absences: a day with no data, a comparison that was held back, a bucket
 * too small to name, a week with nothing in it. Every one of them has to arrive
 * as an explicit empty rather than as a zero.
 */

import { describe, expect, it } from "vitest";

import { patternInsights, weeklyInsights } from "@/test/insightsFakes";

import {
  MEASURE_VALENCE,
  associationTable,
  bucketChartRows,
  bucketTable,
  differenceChartRows,
  formatInterval,
  formatPoints,
  rollingChartRows,
  rollingForMeasure,
  rollingTable,
  trendTable,
  weeklyChartRows,
  weeklyTable,
} from "./series";

const PATTERNS = patternInsights();
const WEEKLY = weeklyInsights();

describe("formatting", () => {
  it("keeps two decimals at most and never invents a resolution", () => {
    expect(formatPoints(3)).toBe("3");
    expect(formatPoints(3.1)).toBe("3.1");
    expect(formatPoints(3.14)).toBe("3.14");
    expect(formatPoints(2.4)).toBe("2.4");
    expect(formatPoints(-0.85)).toBe("-0.85");
    expect(formatPoints(null)).toBeNull();
  });

  it("renders an interval as one string, or nothing when there is not one", () => {
    expect(formatInterval(-1.19, -0.5)).toBe("-1.19 to -0.5");
    expect(formatInterval(null, null)).toBeNull();
    expect(formatInterval(0.3, null)).toBeNull();
  });
});

describe("rolling series", () => {
  it("picks the windows for one measure, shortest first", () => {
    const windows = rollingForMeasure(PATTERNS.rolling, MEASURE_VALENCE);
    expect(windows.map((item) => item.window_days)).toEqual([7, 30]);
    expect(rollingForMeasure(PATTERNS.rolling, "sleep")).toEqual([]);
  });

  it("merges the two windows by day and keeps a gap as a gap", () => {
    const rows = rollingChartRows(rollingForMeasure(PATTERNS.rolling, MEASURE_VALENCE));

    expect(rows).toHaveLength(56);
    expect(rows.map((row) => row.day)).toEqual([...rows.map((row) => row.day)].sort());
    // The fixture's quiet fortnight: no average, and not a zero either.
    const gap = rows[22];
    expect(gap?.short).toBeNull();
    expect(gap?.long).toBeNull();
    const filled = rows[40];
    expect(filled?.short).not.toBeNull();
  });

  it("describes the whole series in a table, including the empty days", () => {
    const table = rollingTable(rollingForMeasure(PATTERNS.rolling, MEASURE_VALENCE), "Mood");

    expect(table.caption).toContain("Mood");
    expect(table.columns.map((column) => column.key)).toEqual(["day", "entries", "short", "long"]);
    expect(table.rows).toHaveLength(56);
    expect(table.rows[22]?.short).toBeNull();
    expect(table.note).toContain("not a zero");
  });
});

describe("tag comparisons", () => {
  it("draws only what was reported, biggest difference first", () => {
    const rows = differenceChartRows(PATTERNS.factors.associations, MEASURE_VALENCE);

    expect(rows.map((row) => row.id)).toEqual(["sleep", "friends"]);
    expect(rows[0]?.difference).toBe(-0.85);
    expect(rows[0]?.ciLow).toBe(-1.19);
    expect(rows[0]?.taggedN).toBe(24);
  });

  it("keeps the held-back comparisons in the table, with their numbers", () => {
    const table = associationTable(PATTERNS.factors.associations, MEASURE_VALENCE, "Mood");

    expect(table.rows).toHaveLength(4);
    const work = table.rows[2];
    expect(work?.factor).toBe("work");
    expect(work?.tagged_n).toBe(3);
    expect(work?.verdict).toBeNull();
    const sleep = table.rows[0];
    expect(sleep?.difference).toBe("-0.85");
    expect(sleep?.p_value).toBe("0.0005");
    expect(sleep?.threshold).toBe("0.0031");
    expect(sleep?.verdict).toContain("24 check-ins vs 34");
    expect(table.note).toContain("did not clear the rules");
  });

  it("leaves a measure with nothing in it as an empty table, not a missing one", () => {
    const table = associationTable(PATTERNS.factors.associations, "energy", "Energy");
    expect(table.rows).toHaveLength(0);
    expect(table.columns.length).toBeGreaterThan(5);
  });
});

describe("groupings", () => {
  it("keeps every bucket, muting the one too small to name", () => {
    const rows = bucketChartRows(PATTERNS.time_of_day, MEASURE_VALENCE);

    expect(rows.map((row) => row.id)).toEqual(["morning", "afternoon", "evening", "night"]);
    const night = rows[3];
    expect(night?.enoughData).toBe(false);
    expect(night?.mean).toBeNull();
    expect(night?.entries).toBe(4);
    // Nothing was reported, so nothing is named.
    expect(rows.every((row) => !row.named)).toBe(true);
  });

  it("marks the two buckets a reported sentence names", () => {
    const reported = {
      ...PATTERNS.time_of_day,
      reported: true,
      lowest_id: "night",
      highest_id: "evening",
      spread: 1,
      buckets: PATTERNS.time_of_day.buckets.map((bucket) =>
        bucket.id === "night" ? { ...bucket, entries: 9, enough_data: true } : bucket,
      ),
    };
    const rows = bucketChartRows(reported, MEASURE_VALENCE);

    expect(rows.filter((row) => row.named).map((row) => row.id)).toEqual(["evening", "night"]);
  });

  it("lists the buckets with both measures and which one was named", () => {
    const table = bucketTable(PATTERNS.weekday);

    expect(table.rows).toHaveLength(7);
    expect(table.rows[0]?.bucket).toBe("Monday");
    expect(table.rows[0]?.valence).toBe("3");
    expect(table.rows[0]?.named).toBeNull();
    expect(table.caption).toContain("day of the week");
    expect(table.note).toContain("small sample");
  });
});

describe("the trend", () => {
  it("shows both windows, the change, the interval and the localised direction", () => {
    const table = trendTable(PATTERNS.trend);

    expect(table.rows).toHaveLength(2);
    const mood = table.rows[0];
    expect(mood?.measure).toBe("Mood");
    expect(mood?.previous).toBe("3");
    expect(mood?.recent).toBe("3.4");
    expect(mood?.change).toBe("0.4");
    expect(mood?.ci).toBe("-0.2 to 1");
    expect(mood?.direction).toBe("About the same");
    expect(mood?.entries).toBe("7 then 7");

    const energy = table.rows[1];
    expect(energy?.direction).toBe("Not enough data yet");
    expect(energy?.change).toBeNull();
    expect(table.note).toContain("not enough data");
  });
});

describe("weeks", () => {
  it("orders the weeks oldest first and empties the one with no check-ins", () => {
    const rows = weeklyChartRows(WEEKLY.weekly);

    expect(rows.map((row) => row.start)).toEqual([
      "2026-09-14",
      "2026-09-21",
      "2026-09-28",
      "2026-10-05",
    ]);
    const empty = rows[2];
    expect(empty?.entries).toBe(0);
    expect(empty?.valence).toBeNull();
    expect(empty?.energy).toBeNull();
  });

  it("counts the tags and emotions of each week, and keeps the empty week", () => {
    const table = weeklyTable(WEEKLY.weekly);

    expect(table.rows).toHaveLength(4);
    expect(table.rows[3]?.week).toBe("5 October 2026");
    expect(table.rows[3]?.factors).toBe("sleep (3), friends (2)");
    expect(table.rows[3]?.emotions).toBe("calm (4)");
    expect(table.rows[2]?.factors).toBeNull();
    expect(table.rows[2]?.entries).toBe(0);
    expect(table.note).toContain("no check-ins");
  });
});
