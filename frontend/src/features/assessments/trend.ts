/**
 * Turning stored results into a chart and a sentence about it.
 *
 * Pure functions, because the honesty of a trend is worth testing without a
 * browser: the points are sorted oldest-first whatever order the API used, a
 * single result never claims a direction, and the sentence is derived from the
 * same numbers the chart draws — so the picture and the words cannot disagree
 * (the Day 16 rule, applied here).
 */

import { formatLocalDay } from "@/lib/dates";

import type { AssessmentBand, HistoryEntry } from "./types";

export interface TrendPoint {
  id: string;
  /** `YYYY-MM-DD`, in the reader's local calendar (the server sends UTC). */
  date: string;
  /** The full timestamp, for the printable summary's exact record. */
  at: string;
  total: number;
  maxTotal: number;
  band: AssessmentBand;
  bandLabel: string;
}

/** Local calendar day of an instant, without the UTC-midnight trap. */
export function localDay(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) {
    return iso;
  }
  const year = at.getFullYear();
  const month = `${at.getMonth() + 1}`.padStart(2, "0");
  const day = `${at.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Oldest first, whatever order the API returned. */
export function buildTrend(entries: readonly HistoryEntry[]): TrendPoint[] {
  return [...entries]
    .sort((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at))
    .map((entry) => ({
      id: entry.id,
      date: localDay(entry.created_at),
      at: entry.created_at,
      total: entry.total,
      maxTotal: entry.max_total,
      band: entry.band,
      bandLabel: entry.band_label,
    }));
}

/** The band boundaries drawn as reference lines, excluding zero. */
export function cutpoints(bands: readonly { min: number }[]): number[] {
  return [...new Set(bands.map((band) => band.min))]
    .filter((value) => value > 0)
    .sort((a, b) => a - b);
}

export function formatPointDate(iso: string): string {
  return /^\d{4}-\d{2}-\d{2}$/.test(iso) ? formatLocalDay(iso) : iso;
}
