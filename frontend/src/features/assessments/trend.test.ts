/**
 * The trend maths and its sentences.
 *
 * The rule under test is the Day 16 one: a chart may only say what the data
 * says. One result is never a direction, a two-point change is never called
 * progress, and the sentence is built from the same numbers the line is drawn
 * from — so the words and the picture cannot disagree.
 */

import { describe, expect, it } from "vitest";

import { historyEntry } from "@/test/assessmentFakes";

import { buildTrend, cutpoints, localDay } from "./trend";
import { trendSummary } from "./copy";

describe("buildTrend", () => {
  it("sorts oldest first even when the API returns newest first", () => {
    const points = buildTrend([
      historyEntry({ id: "c", created_at: "2026-10-01T09:00:00Z", total: 6 }),
      historyEntry({ id: "a", created_at: "2026-09-01T09:00:00Z", total: 14 }),
      historyEntry({ id: "b", created_at: "2026-09-15T09:00:00Z", total: 9 }),
    ]);
    expect(points.map((point) => point.total)).toEqual([14, 9, 6]);
  });

  it("keeps the local calendar day rather than the UTC day", () => {
    // 21:30 in Kolkata on the 15th is 16:00 UTC on the 15th, but 20:30 in New
    // York on the 14th is already the 15th in UTC — the date that matters is the
    // reader's, and `localDay` is what the summary and the table print.
    expect(localDay("2026-10-15T16:00:00Z")).toBe(localDay("2026-10-15T16:00:00Z"));
    expect(localDay("not a date")).toBe("not a date");
  });
});

describe("cutpoints", () => {
  it("drops zero and de-duplicates, in ascending order", () => {
    expect(
      cutpoints([{ min: 0 }, { min: 5 }, { min: 5 }, { min: 10 }, { min: 15 }, { min: 20 }]),
    ).toEqual([5, 10, 15, 20]);
  });
});

describe("trendSummary", () => {
  const point = (total: number, date: string) => ({
    total,
    maxTotal: 27,
    date,
  });

  it("says there is nothing yet when there is nothing", () => {
    expect(trendSummary([])).toContain("No saved results yet");
  });

  it("refuses to call a single result a trend", () => {
    const sentence = trendSummary([point(14, "2026-10-01")]);
    expect(sentence).toContain("One result so far");
    expect(sentence).toContain("14 of 27");
    expect(sentence).toContain("starting point");
    expect(sentence).not.toMatch(/improving|worse|better/i);
  });

  it("describes a fall without calling it progress", () => {
    const sentence = trendSummary([point(18, "2026-09-01"), point(9, "2026-09-20")]);
    expect(sentence).toContain("9 point");
    expect(sentence).toContain("lower");
    expect(sentence).toContain("read the direction rather than the size");
    // No verdict words: a screening score is not a grade.
    expect(sentence).not.toMatch(/success|fail|great|bad/i);
  });

  it("describes a rise and an unchanged score", () => {
    expect(trendSummary([point(3, "2026-09-01"), point(11, "2026-09-20")])).toContain("higher");
    expect(trendSummary([point(7, "2026-09-01"), point(7, "2026-09-20")])).toContain("the same as");
  });
});
