/**
 * Day 42 — the TypeScript signal pipeline against the NumPy reference.
 *
 * `scripts/day42_hrv_reference.py` re-implements the documented algorithm
 * (ADR 0041 §Signal) independently, on committed fixtures, and writes
 * `fixtures/reference.json` with NumPy-computed values. This test asserts
 * agreement to 1e-9 ms and **prints the actual error** so the verification
 * record (docs/verification/day42-biofeedback-hrv.md) can quote it.
 *
 * If the fixtures ever need regenerating:
 *   python scripts/day42_hrv_reference.py
 */

import { describe, expect, it } from "vitest";

import { rejectArtefacts } from "./artefact";
import { meanHeartRateBpm, rmssdMs, slidingWindowMetrics } from "./metrics";
import { assessQuality, qualityScore } from "./quality";
import type { CleanSeries, RRSample, WindowMetrics } from "./types";

import cleanFixture from "./fixtures/clean-rsa.json";
import noisyFixture from "./fixtures/noisy-ectopic-dropouts.json";
import reference from "./fixtures/reference.json";

interface Fixture {
  name: string;
  description: string;
  samples: Array<{ rrMs: number; tMs: number }>;
}

interface ReferenceWindow {
  startMs: number;
  endMs: number;
  acceptedBeats: number;
  rejectedBeats: number;
  gapMs: number;
  quality: string;
  qualityScore: number;
  rmssdMs: number | null;
  meanHrBpm: number | null;
}

interface ReferenceBlock {
  acceptedCount: number;
  rejectedCount: number;
  rejected: Array<{ rawIndex: number; reason: string }>;
  gaps: Array<{ afterRawIndex: number; gapMs: number; estimatedMissingBeats: number }>;
  flatlineFraction: number;
  fullSeries: {
    durationMs: number;
    quality: string;
    qualityScore: number;
    rmssdMs: number | null;
    meanHrBpm: number | null;
  };
  windows: ReferenceWindow[];
}

const TOLERANCE = 1e-9;

const FIXTURES: Array<[Fixture, ReferenceBlock]> = [
  [cleanFixture as Fixture, reference["clean-rsa"] as unknown as ReferenceBlock],
  [noisyFixture as Fixture, reference["noisy-ectopic-dropouts"] as unknown as ReferenceBlock],
];

/** Full-series comparison inputs, computed the way the reference does. */
function fullSeriesInputs(series: CleanSeries): {
  quality: ReturnType<typeof assessQuality>;
  rmssd: number | null;
  meanHr: number | null;
} {
  const durationMs =
    (series.accepted[series.accepted.length - 1]?.tMs ?? 0) - (series.accepted[0]?.tMs ?? 0);
  const pairs = flatPairs(series);
  const quality = assessQuality({
    acceptedBeats: series.accepted.length,
    rejectedBeats: series.rejected.length,
    gapMs: series.gaps.reduce((acc, g) => acc + g.gapMs, 0),
    windowMs: durationMs,
    contactDetected: null,
    minBeats: 15,
    flatlineFraction: pairs === 0 ? 0 : pairs.flat / pairs.total,
  });
  const withheld = quality.level === "poor";
  return {
    quality,
    rmssd: withheld ? null : rmssdMs(series.accepted, 10),
    meanHr: withheld ? null : meanHeartRateBpm(series.accepted),
  };
}

function flatPairs(series: CleanSeries): { flat: number; total: number } {
  let flat = 0;
  let total = 0;
  for (let i = 0; i + 1 < series.accepted.length; i += 1) {
    const next = series.accepted[i + 1];
    const current = series.accepted[i];
    if (current === undefined || next === undefined || next.breakBefore) {
      continue;
    }
    total += 1;
    if (Math.abs(next.rrMs - current.rrMs) < 1) {
      flat += 1;
    }
  }
  return { flat, total };
}

describe("TS pipeline vs NumPy reference", () => {
  for (const [fixture, ref] of FIXTURES) {
    describe(fixture.name, () => {
      const samples = fixture.samples as RRSample[];
      const series = rejectArtefacts(samples);

      it("rejects exactly the same beats for the same reasons", () => {
        const got = series.rejected.map((r) => ({ rawIndex: r.rawIndex, reason: r.reason }));
        expect(got).toEqual(ref.rejected);
        expect(series.accepted.length).toBe(ref.acceptedCount);
        expect(series.rejected.length).toBe(ref.rejectedCount);
      });

      it("finds the same timeline gaps", () => {
        expect(series.gaps.length).toBe(ref.gaps.length);
        series.gaps.forEach((g, i) => {
          const r = ref.gaps[i];
          expect(r).toBeDefined();
          if (r === undefined) {
            return;
          }
          expect(g.afterRawIndex).toBe(r.afterRawIndex);
          // Floats across languages are compared with tolerance, never deep-equal.
          expect(Math.abs(g.gapMs - r.gapMs)).toBeLessThan(TOLERANCE);
          expect(g.estimatedMissingBeats).toBe(r.estimatedMissingBeats);
        });
      });

      it("matches RMSSD and mean HR on the full series (error printed)", () => {
        const full = fullSeriesInputs(series);
        const errors: string[] = [];
        if (ref.fullSeries.rmssdMs === null) {
          expect(full.rmssd).toBeNull();
        } else {
          expect(full.rmssd).not.toBeNull();
          const error = Math.abs((full.rmssd ?? 0) - ref.fullSeries.rmssdMs);
          errors.push(`rmssdMs ts=${full.rmssd} ref=${ref.fullSeries.rmssdMs} |err|=${error}`);
          expect(error).toBeLessThan(TOLERANCE);
        }
        if (ref.fullSeries.meanHrBpm === null) {
          expect(full.meanHr).toBeNull();
        } else {
          expect(full.meanHr).not.toBeNull();
          const error = Math.abs((full.meanHr ?? 0) - ref.fullSeries.meanHrBpm);
          errors.push(`meanHrBpm ts=${full.meanHr} ref=${ref.fullSeries.meanHrBpm} |err|=${error}`);
          expect(error).toBeLessThan(TOLERANCE);
        }
        const scoreError = Math.abs(qualityScore({
          acceptedBeats: series.accepted.length,
          rejectedBeats: series.rejected.length,
          gapMs: series.gaps.reduce((acc, g) => acc + g.gapMs, 0),
          windowMs: ref.fullSeries.durationMs,
          contactDetected: null,
          minBeats: 15,
          flatlineFraction: flatPairs(series).total === 0 ? 0 : flatPairs(series).flat / flatPairs(series).total,
        }) - ref.fullSeries.qualityScore);
        errors.push(`qualityScore ts≈ ref=${ref.fullSeries.qualityScore} |err|=${scoreError}`);
        expect(scoreError).toBeLessThan(TOLERANCE);
        expect(full.quality.level).toBe(ref.fullSeries.quality);
        console.log(`[reference:${fixture.name}] ${errors.join("  ;  ")}`);
      });

      it("matches every sliding window (metrics and the quality gate)", () => {
        const windows = slidingWindowMetrics(series, {
          windowMs: 60_000,
          stepMs: 30_000,
          minBeats: 15,
          minSuccessivePairs: 10,
          contactDetected: null,
        });
        expect(windows.length).toBe(ref.windows.length);
        const rows: string[] = [];
        windows.forEach((w: WindowMetrics, i: number) => {
          const r = ref.windows[i];
          expect(r).toBeDefined();
          if (r === undefined) {
            return;
          }
          expect(w.acceptedBeats).toBe(r.acceptedBeats);
          expect(w.rejectedBeats).toBe(r.rejectedBeats);
          expect(Math.abs(w.gapMs - r.gapMs)).toBeLessThan(TOLERANCE);
          expect(w.quality).toBe(r.quality);
          expect(Math.abs(w.qualityScore - r.qualityScore)).toBeLessThan(TOLERANCE);
          if (r.rmssdMs === null) {
            expect(w.rmssdMs).toBeNull();
          } else {
            expect(w.rmssdMs).not.toBeNull();
            expect(Math.abs((w.rmssdMs ?? 0) - r.rmssdMs)).toBeLessThan(TOLERANCE);
          }
          if (r.meanHrBpm === null) {
            expect(w.meanHrBpm).toBeNull();
          } else {
            expect(w.meanHrBpm).not.toBeNull();
            expect(Math.abs((w.meanHrBpm ?? 0) - r.meanHrBpm)).toBeLessThan(TOLERANCE);
          }
          rows.push(
            `${Math.round(w.startMs / 1000)}s acc=${w.acceptedBeats} rej=${w.rejectedBeats} ` +
              `gap=${Math.round(w.gapMs)} q=${w.quality}(${w.qualityScore.toFixed(3)}) ` +
              `rmssd=${w.rmssdMs === null ? "withheld" : w.rmssdMs.toFixed(3)}`,
          );
        });
        console.log(`[windows:${fixture.name}]\n  ${rows.join("\n  ")}`);
      });
    });
  }
});
