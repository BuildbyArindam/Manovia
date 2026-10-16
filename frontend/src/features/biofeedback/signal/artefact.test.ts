/**
 * Artefact rejection tests (Day 42): range filter, ectopic filter, gap
 * marking, and the break-before contract the metrics stage depends on.
 */

import { describe, expect, it } from "vitest";

import { DEFAULT_ARTEFACT_OPTIONS, detectGaps, rejectArtefacts } from "./artefact";
import type { RRSample } from "./types";

function series(values: number[], startMs = 0): RRSample[] {
  let t = startMs;
  return values.map((rrMs) => {
    t += rrMs;
    return { rrMs, tMs: t };
  });
}

describe("rejectArtefacts", () => {
  it("keeps a clean series untouched", () => {
    const clean = rejectArtefacts(series([800, 810, 790, 805, 800, 795, 800, 810, 805, 800]));
    expect(clean.rejected).toEqual([]);
    expect(clean.accepted.length).toBe(10);
    expect(clean.accepted.every((s) => s.breakBefore === false)).toBe(true);
  });

  it("removes out-of-range intervals and records why", () => {
    const out = rejectArtefacts(series([800, 250, 800, 2300, 800, 810, 800, 805, 800, 795]));
    expect(out.rejected.map((r) => [r.rawIndex, r.reason])).toEqual([
      [1, "out-of-range"],
      [3, "out-of-range"],
    ]);
  });

  it("removes ectopic beats by local median deviation", () => {
    // A classic ectopic pair: one premature beat, one compensatory pause.
    const out = rejectArtefacts(
      series([800, 805, 795, 800, 620, 980, 800, 805, 795, 800, 805, 800]),
    );
    expect(out.rejected.map((r) => [r.rawIndex, r.reason])).toEqual([
      [4, "ectopic"],
      [5, "ectopic"],
    ]);
  });

  it("keeps a value exactly at the 20% threshold (strict inequality)", () => {
    // 800 -> 640 is exactly 20% below the local median: NOT rejected.
    const out = rejectArtefacts(
      series([800, 800, 800, 640, 800, 800, 800, 800, 800, 800, 800, 800]),
    );
    expect(out.rejected).toEqual([]);
  });

  it("marks a fresh chain after every removal", () => {
    const out = rejectArtefacts(series([800, 805, 795, 800, 620, 980, 800, 805, 795, 800, 805, 800]));
    // Accepted raw indices: 0,1,2,3,6,7,8,9,10,11 — break before 6 (after 3).
    const breaks = out.accepted.filter((s) => s.breakBefore).map((s) => s.rawIndex);
    expect(breaks).toEqual([6]);
  });

  it("detects a timestamp gap as missing beats", () => {
    const samples: RRSample[] = [];
    let t = 0;
    for (const rr of [800, 800, 800, 800, 800]) {
      t += rr;
      samples.push({ rrMs: rr, tMs: t });
    }
    // 3.8 s vanish from the record, then the beats continue.
    t += 3800 + 800;
    samples.push({ rrMs: 800, tMs: t });
    for (const rr of [800, 800, 800, 800]) {
      t += rr;
      samples.push({ rrMs: rr, tMs: t });
    }
    const out = rejectArtefacts(samples);
    expect(out.gaps.length).toBe(1);
    expect(out.gaps[0]?.afterRawIndex).toBe(4);
    expect(out.gaps[0]?.gapMs).toBe(3800);
    expect(out.gaps[0]?.estimatedMissingBeats).toBe(4);
    expect(out.accepted.find((s) => s.rawIndex === 5)?.breakBefore).toBe(true);
    // The beats themselves are fine and are kept.
    expect(out.rejected).toEqual([]);
  });

  it("honours an explicit breakBefore flag even when spacing looks continuous", () => {
    const samples = series([800, 800, 800, 800, 800, 800, 800, 800, 800, 800]);
    const flagged = samples.map((s, i) => (i === 5 ? { ...s, breakBefore: true } : s));
    const out = rejectArtefacts(flagged);
    expect(out.gaps.length).toBe(1);
    expect(out.accepted.find((s) => s.rawIndex === 5)?.breakBefore).toBe(true);
  });
});

describe("detectGaps", () => {
  it("finds nothing in an uninterrupted series", () => {
    const { gaps, breakAfter } = detectGaps(series([800, 810, 790, 805]));
    expect(gaps).toEqual([]);
    expect(breakAfter.size).toBe(0);
  });

  it("exposes its thresholds as documented constants", () => {
    expect(DEFAULT_ARTEFACT_OPTIONS.minRrMs).toBe(300);
    expect(DEFAULT_ARTEFACT_OPTIONS.maxRrMs).toBe(2000);
    expect(DEFAULT_ARTEFACT_OPTIONS.ectopicRelThreshold).toBe(0.2);
  });
});
