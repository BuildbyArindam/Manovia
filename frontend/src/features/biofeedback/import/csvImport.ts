/**
 * CSV import — the no-hardware path (Day 42).
 *
 * Plenty of people have heart-rate data in a health app and no compatible
 * strap. This parses a CSV export in the browser, on this device, and feeds
 * the exact same signal pipeline as a live sensor. **No upload, no fetch, no
 * analytics** — the module below has no network API in its imports on purpose
 * (`csvImport.localOnly.test.ts` pins that).
 *
 * Supported shapes (auto-detected from the header row):
 *
 *   RR intervals, ms     columns: rr_ms | rrms | rr | rr_interval | rr_interval_ms
 *   RR intervals, s      columns: rr_interval_s | rr_s
 *   RR raw 1/1024 s      columns: rr_1024 | rr_raw        (FIT-style raw units)
 *   Heart rate, bpm      columns: hr | heart_rate | bpm | heartrate
 *                        (converted to RR = 60000/hr when no RR column exists)
 *   Timestamps           columns: timestamp | time | datetime | date_time
 *                        (ISO 8601, epoch seconds, or epoch milliseconds;
 *                        when absent the intervals are laid end to end)
 *
 * `#` comment lines (many exporters write provenance there) and blank lines are
 * skipped. Comma, semicolon and tab delimiters are accepted. Anything else is
 * refused with a message naming what was found, not guessed at.
 *
 * WELLNESS FEATURE. Imported data is used for breathing biofeedback and a
 * personal trend view; it is not a medical record import and no health
 * conclusion is drawn from it.
 */

import { rrSamplesFromIntervals } from "../signal/rr";
import type { RRSample } from "../signal/types";

export class CsvImportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CsvImportError";
  }
}

export interface CsvImportResult {
  samples: RRSample[];
  /** How the columns were interpreted, for the UI to show back. */
  mapping: {
    rrColumn: string | null;
    rrUnit: "ms" | "s" | "1/1024s" | null;
    hrColumn: string | null;
    timeColumn: string | null;
  };
  /** Rows skipped (wrong value, unparseable time), for honest reporting. */
  skippedRows: number;
  warnings: string[];
}

const RR_MS_ALIASES = ["rr_ms", "rrms", "rr", "rr_interval", "rr_interval_ms", "rr interval (ms)"];
const RR_S_ALIASES = ["rr_interval_s", "rr_s", "rrs"];
const RR_1024_ALIASES = ["rr_1024", "rr_raw", "rrinterval_raw"];
const HR_ALIASES = ["hr", "heart_rate", "heart rate", "heartrate", "bpm"];
const TIME_ALIASES = ["timestamp", "time", "datetime", "date_time", "datetime_utc"];

function normalizeHeader(value: string): string {
  return value.trim().toLowerCase().replace(/"/g, "").replace(/\s+/g, " ");
}

function detectDelimiter(firstLine: string): string {
  const counts: Array<[string, number]> = [
    [",", (firstLine.match(/,/g) ?? []).length],
    [";", (firstLine.match(/;/g) ?? []).length],
    ["\t", (firstLine.match(/\t/g) ?? []).length],
  ];
  counts.sort((a, b) => b[1] - a[1]);
  const best = counts[0];
  if (best === undefined || best[1] === 0) {
    // No delimiter at all: a single-column CSV is still a CSV (e.g. just rr_ms).
    return ",";
  }
  return best[0];
}

function parseTimestamp(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") {
    return null;
  }
  if (/^-?\d+$/.test(trimmed)) {
    const n = Number(trimmed);
    // Heuristic, documented: >= 1e12 is epoch milliseconds, else seconds.
    return n >= 1e12 ? n : n * 1000;
  }
  const parsed = Date.parse(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * Parse a CSV export into RR samples. Throws {@link CsvImportError} when the
 * file has no recognisable heart-rate columns; returns warnings for rows it
 * had to skip.
 */
export function parseHrCsv(text: string): CsvImportResult {
  const lines = text
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#"));

  if (lines.length < 2) {
    throw new CsvImportError("the file needs a header row and at least one data row");
  }
  const delimiter = detectDelimiter(lines[0] ?? "");
  const header = (lines[0] ?? "").split(delimiter).map(normalizeHeader);

  const find = (aliases: string[]): { name: string; index: number } | null => {
    for (const alias of aliases) {
      const index = header.indexOf(alias);
      if (index !== -1) {
        return { name: header[index] ?? alias, index };
      }
    }
    return null;
  };

  const rrMsCol = find(RR_MS_ALIASES);
  const rrSCol = find(RR_S_ALIASES);
  const rr1024Col = find(RR_1024_ALIASES);
  const hrCol = find(HR_ALIASES);
  const timeCol = find(TIME_ALIASES);

  const rrCol = rrMsCol ?? rrSCol ?? rr1024Col;
  const rrUnit: CsvImportResult["mapping"]["rrUnit"] =
    rrMsCol !== null ? "ms" : rrSCol !== null ? "s" : rr1024Col !== null ? "1/1024s" : null;

  if (rrCol === null && hrCol === null) {
    throw new CsvImportError(
      `no RR-interval or heart-rate column found (looked for rr_ms/rr/heart_rate/hr/bpm; ` +
        `columns were: ${header.join(", ") || "none"})`,
    );
  }

  const warnings: string[] = [];
  if (rrCol === null && hrCol !== null) {
    warnings.push("no RR column: intervals derived from heart rate (60000/hr)");
  }
  if (timeCol === null) {
    warnings.push("no timestamp column: intervals laid end to end from 0");
  }

  const samples: RRSample[] = [];
  const explicitTimes: number[] = [];
  let skippedRows = 0;

  for (let i = 1; i < lines.length; i += 1) {
    const cells = (lines[i] ?? "").split(delimiter);
    let rrMs: number | null = null;
    if (rrCol !== null) {
      const raw = Number((cells[rrCol.index] ?? "").trim());
      if (!Number.isFinite(raw) || raw <= 0) {
        skippedRows += 1;
        continue;
      }
      rrMs = rrUnit === "s" ? raw * 1000 : rrUnit === "1/1024s" ? (raw * 1000) / 1024 : raw;
    } else if (hrCol !== null) {
      const bpm = Number((cells[hrCol.index] ?? "").trim());
      if (!Number.isFinite(bpm) || bpm <= 0 || bpm > 300) {
        skippedRows += 1;
        continue;
      }
      rrMs = 60_000 / bpm;
    }
    if (rrMs === null || !Number.isFinite(rrMs)) {
      skippedRows += 1;
      continue;
    }
    if (timeCol !== null) {
      const t = parseTimestamp(cells[timeCol.index] ?? "");
      if (t === null) {
        skippedRows += 1;
        continue;
      }
      explicitTimes.push(t);
    }
    samples.push({ rrMs: Math.round(rrMs * 1000) / 1000, tMs: 0 });
  }

  if (samples.length === 0) {
    throw new CsvImportError("no usable rows found");
  }
  if (skippedRows > 0) {
    warnings.push(`${skippedRows} row(s) skipped (missing or unparseable values)`);
  }

  if (timeCol !== null && explicitTimes.length === samples.length) {
    // Timestamps win: they carry dropouts the way a real export does.
    for (let i = 0; i < samples.length; i += 1) {
      const sample = samples[i];
      if (sample !== undefined) {
        sample.tMs = explicitTimes[i] ?? 0;
      }
    }
  } else {
    const laid = rrSamplesFromIntervals(samples.map((s) => s.rrMs), 0);
    for (let i = 0; i < samples.length; i += 1) {
      const sample = samples[i];
      const laidSample = laid[i];
      if (sample !== undefined && laidSample !== undefined) {
        sample.tMs = laidSample.tMs;
      }
    }
  }

  return {
    samples,
    mapping: {
      rrColumn: rrCol?.name ?? null,
      rrUnit,
      hrColumn: hrCol?.name ?? null,
      timeColumn: timeCol?.name ?? null,
    },
    skippedRows,
    warnings,
  };
}
