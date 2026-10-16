/**
 * CSV import tests (Day 42): the no-hardware path, end to end through the
 * same signal pipeline the live sensor uses.
 */

import { describe, expect, it } from "vitest";

import { CsvImportError, parseHrCsv } from "./csvImport";
import { rejectArtefacts } from "../signal/artefact";
import { slidingWindowMetrics } from "../signal/metrics";

import fixtureCsv from "./fixtures/health-export-sample.csv?raw";

describe("parseHrCsv", () => {
  it("parses the sample health-app export (timestamp + rr_ms)", () => {
    const result = parseHrCsv(fixtureCsv);
    expect(result.samples.length).toBe(40);
    expect(result.mapping.rrColumn).toBe("rr_ms");
    expect(result.mapping.rrUnit).toBe("ms");
    expect(result.mapping.timeColumn).toBe("timestamp");
    expect(result.skippedRows).toBe(0);
    // First interval ends at its timestamp (absolute epoch ms).
    expect(new Date(result.samples[0]?.tMs ?? 0).toISOString()).toBe("2026-10-16T18:00:00.797Z");
  });

  it("feeds the shared signal pipeline: 2 artefacts, no gaps", () => {
    const result = parseHrCsv(fixtureCsv);
    const series = rejectArtefacts(result.samples);
    expect(series.rejected.map((r) => r.reason).sort()).toEqual(["ectopic", "out-of-range"]);
    expect(series.gaps).toEqual([]);
    const windows = slidingWindowMetrics(series);
    expect(windows.length).toBeGreaterThan(0);
  });

  it("understands seconds and 1/1024 s RR columns", () => {
    const seconds = parseHrCsv("rr_interval_s\n0.8\n0.82\n0.78\n0.8\n0.81\n0.79\n0.8\n0.81\n");
    expect(seconds.samples.map((s) => s.rrMs)).toEqual([800, 820, 780, 800, 810, 790, 800, 810]);
    const raw = parseHrCsv("rr_1024\n1024\n819.2\n1024\n819.2\n");
    expect(raw.samples.map((s) => s.rrMs)).toEqual([1000, 800, 1000, 800]);
  });

  it("derives RR from heart rate when no RR column exists", () => {
    const result = parseHrCsv("timestamp,hr\n2026-10-16T18:00:00Z,60\n2026-10-16T18:00:01Z,75\n");
    expect(result.mapping.hrColumn).toBe("hr");
    expect(result.warnings.join(" ")).toContain("derived from heart rate");
    expect(result.samples.map((s) => s.rrMs)).toEqual([1000, 800]);
  });

  it("lays intervals end to end when there is no timestamp column", () => {
    const result = parseHrCsv("rr_ms\n800\n800\n800\n");
    expect(result.samples.map((s) => s.tMs)).toEqual([800, 1600, 2400]);
    expect(result.warnings.join(" ")).toContain("laid end to end");
  });

  it("skips bad rows and says how many", () => {
    const result = parseHrCsv("rr_ms\n800\nnot-a-number\n\n800\n-5\n800\n");
    expect(result.samples.length).toBe(3);
    expect(result.skippedRows).toBe(2);
    expect(result.warnings.join(" ")).toContain("2 row(s) skipped");
  });

  it("refuses files it does not recognise, naming what it found", () => {
    expect(() => parseHrCsv("a,b\n1,2\n")).toThrow(CsvImportError);
    expect(() => parseHrCsv("a,b\n1,2\n")).toThrow(/no RR-interval or heart-rate column/);
    expect(() => parseHrCsv("rr_ms")).toThrow(/header row/);
    expect(() => parseHrCsv("nonsense\nmore nonsense\n")).toThrow(/columns were: nonsense/);
    expect(() => parseHrCsv("rr_ms\nbad\nworse\n")).toThrow(/no usable rows/);
  });

  it("handles semicolon and tab delimiters", () => {
    const semi = parseHrCsv("rr_ms;hr\n800;75\n810;74\n");
    expect(semi.samples.length).toBe(2);
    const tabbed = parseHrCsv("rr_ms\thr\n800\t75\n810\t74\n");
    expect(tabbed.samples.length).toBe(2);
  });

  it("skips comment lines and a BOM", () => {
    const result = parseHrCsv("\uFEFF# exported by some app\nrr_ms\n800\n810\n# trailing note\n");
    expect(result.samples.map((s) => s.rrMs)).toEqual([800, 810]);
  });

  it("preserves timestamped gaps so the pipeline can see dropouts", () => {
    const result = parseHrCsv(
      "timestamp,rr_ms\n" +
        "2026-10-16T18:00:00.800Z,800\n" +
        "2026-10-16T18:00:01.600Z,800\n" +
        // 3 s of the recording are missing from the export
        "2026-10-16T18:00:05.400Z,800\n" +
        "2026-10-16T18:00:06.200Z,800\n",
    );
    const series = rejectArtefacts(result.samples);
    expect(series.gaps.length).toBe(1);
    expect(series.gaps[0]?.estimatedMissingBeats).toBeGreaterThan(1);
    expect(series.accepted.find((s) => s.tMs === Date.parse("2026-10-16T18:00:05.400Z"))?.breakBefore).toBe(true);
  });
});
