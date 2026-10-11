/**
 * Turning an insights response into rows: chart rows, table rows, CSV rows.
 *
 * Every function here is pure and takes only what the server sent. That is the
 * point of keeping them in one file away from the components — a chart and its
 * data table have to show *the same numbers*, and the only way to guarantee that
 * is to build both from one place. If a component shaped its own rows, the table
 * and the picture could drift apart, and a data table that disagrees with its
 * chart is worse than no table at all.
 *
 * Two shaping rules worth knowing before reading the code:
 *
 * - **Charts draw only what the server is willing to say.** A factor chart shows
 *   reported comparisons; a comparison that was held back appears in the table,
 *   with its reason, and not in the picture.
 * - **Tables show everything.** A bucket with three check-ins is still somebody's
 *   own number, so it is in the table (muted in the chart, flagged in a column),
 *   because a table of your own data is not a claim.
 */

import type {
  Association,
  GroupBucket,
  GroupPattern,
  Rolling,
  RollingPoint,
  Trend,
  Week,
} from "./types";

/** One column of a data table. `numeric` right-aligns it and marks it in HTML. */
export interface DataTableColumn {
  readonly key: string;
  readonly label: string;
  readonly numeric?: boolean;
}

/** One row, keyed by column key. `null` renders as an em dash. */
export type DataTableRow = Record<string, string | number | null>;

export interface DataTable {
  readonly caption: string;
  readonly columns: readonly DataTableColumn[];
  readonly rows: readonly DataTableRow[];
  /**
   * A note about how to read the table — what a dash means, what "held back"
   * means. Rendered next to the table, and included in the CSV as a comment
   * line so an exported file does not lose it.
   */
  readonly note?: string;
}

/** One point of a rolling-average line chart, after merging the two windows. */
export interface RollingRow {
  readonly day: string;
  readonly short: number | null;
  readonly long: number | null;
}

/** One bar of a factor chart: a difference the server is willing to report. */
export interface DifferenceRow {
  readonly id: string;
  readonly label: string;
  readonly difference: number;
  readonly ciLow: number | null;
  readonly ciHigh: number | null;
  readonly taggedN: number;
  readonly untaggedN: number;
}

/** One bar of a grouping chart. */
export interface BucketRow {
  readonly id: string;
  readonly label: string;
  readonly mean: number | null;
  readonly entries: number;
  readonly enoughData: boolean;
  readonly named: boolean;
}

/** One bar of the week-by-week chart. */
export interface WeekRow {
  readonly start: string;
  readonly label: string;
  readonly valence: number | null;
  readonly energy: number | null;
  readonly entries: number;
}

export const MEASURE_VALENCE = "valence";
export const MEASURE_ENERGY = "energy";

/** Digits in tables and CSV. The sentences carry the localised spelling. */
export function formatPoints(value: number | null): string | null {
  if (value === null) {
    return null;
  }
  // Two decimals is the precision a 1-5 rating can carry; three would imply a
  // resolution the check-in never had.
  const rounded = Math.round(value * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2).replace(/0$/, "");
}

/** A confidence interval as one string, for a table cell. */
export function formatInterval(low: number | null, high: number | null): string | null {
  const from = formatPoints(low);
  const to = formatPoints(high);
  if (from === null || to === null) {
    return null;
  }
  return `${from} to ${to}`;
}

/** The rolling averages for one measure, shortest window first. */
export function rollingForMeasure(rolling: readonly Rolling[], measure: string): Rolling[] {
  return rolling
    .filter((item) => item.measure === measure)
    .sort((left, right) => left.window_days - right.window_days);
}

/**
 * Merge the two windows of one measure into rows a line chart can draw.
 *
 * Both series cover the same 56 days, so the merge is by date; a day where a
 * window has too few check-ins keeps `null` and the line has a gap there rather
 * than a dip to zero. A gap that means "we do not know" is the honest rendering,
 * and the table underneath says the same thing in words.
 */
export function rollingChartRows(windows: readonly Rolling[]): RollingRow[] {
  const days = new Map<string, { short: number | null; long: number | null }>();
  const [shortest, longest] = windows;

  const absorb = (series: readonly RollingPoint[], key: "short" | "long"): void => {
    for (const point of series) {
      const row = days.get(point.day) ?? { short: null, long: null };
      row[key] = point.enough_data ? point.average : null;
      days.set(point.day, row);
    }
  };
  if (shortest !== undefined) {
    absorb(shortest.series, "short");
  }
  if (longest !== undefined) {
    absorb(longest.series, "long");
  }

  return [...days.entries()]
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([day, value]) => ({ day, short: value.short, long: value.long }));
}

/** The rolling series as a table: one row per day, both windows side by side. */
export function rollingTable(windows: readonly Rolling[], measureLabel: string): DataTable {
  const [shortest, longest] = windows;
  const shortLabel = `${shortest?.window_days ?? 7}-day average`;
  const longLabel = `${longest?.window_days ?? 30}-day average`;
  const rows = rollingChartRows(windows).map((row) => ({
    day: row.day,
    short: formatPoints(row.short),
    long: formatPoints(row.long),
    entries: shortest?.series.find((point) => point.day === row.day)?.entries ?? null,
  }));

  return {
    caption: `${measureLabel}: every day of the rolling averages behind the chart`,
    columns: [
      { key: "day", label: "Day" },
      { key: "entries", label: "Check-ins that day", numeric: true },
      { key: "short", label: shortLabel, numeric: true },
      { key: "long", label: longLabel, numeric: true },
    ],
    rows,
    note: "An empty average means that day had too few check-ins for a rolling window to include. It is not a zero.",
  };
}

/** The reported comparisons for one measure, biggest difference first. */
export function differenceChartRows(
  associations: readonly Association[],
  measure: string,
): DifferenceRow[] {
  return associations
    .filter((item) => item.measure === measure && item.reported && item.difference !== null)
    .sort((left, right) => Math.abs(right.difference ?? 0) - Math.abs(left.difference ?? 0))
    .map((item) => ({
      id: item.factor,
      label: item.label,
      difference: item.difference ?? 0,
      ciLow: item.ci_low,
      ciHigh: item.ci_high,
      taggedN: item.tagged_n,
      untaggedN: item.untagged_n,
    }));
}

/**
 * Every comparison for one measure, reported or not.
 *
 * This table is the "show your work" surface: sample sizes on both sides, both
 * means, the difference, its interval, the p-value, the corrected threshold it
 * was judged under, and — when nothing was claimed — the reason.
 */
export function associationTable(
  associations: readonly Association[],
  measure: string,
  measureLabel: string,
): DataTable {
  const rows = associations
    .filter((item) => item.measure === measure)
    .map((item) => ({
      factor: item.label,
      tagged_n: item.tagged_n,
      untagged_n: item.untagged_n,
      tagged_mean: formatPoints(item.tagged_mean),
      untagged_mean: formatPoints(item.untagged_mean),
      difference: formatPoints(item.difference),
      ci: formatInterval(item.ci_low, item.ci_high),
      p_value: item.p_value === null ? null : item.p_value.toFixed(4),
      threshold: item.family_alpha.toFixed(4),
      verdict: item.reported ? item.sentence : null,
    }));

  return {
    caption: `Days with each tag compared with days without it, for ${measureLabel}`,
    columns: [
      { key: "factor", label: "Tag" },
      { key: "tagged_n", label: "Check-ins with the tag", numeric: true },
      { key: "untagged_n", label: "Check-ins without it", numeric: true },
      { key: "tagged_mean", label: "Average with the tag", numeric: true },
      { key: "untagged_mean", label: "Average without it", numeric: true },
      { key: "difference", label: "Difference (points)", numeric: true },
      { key: "ci", label: "95% interval for the difference" },
      { key: "p_value", label: "p (shuffled)", numeric: true },
      { key: "threshold", label: "Threshold after correction", numeric: true },
      { key: "verdict", label: "What we can say" },
    ],
    rows,
    note: "An empty last column means this comparison did not clear the rules: too few check-ins on one side, a difference smaller than the minimum worth mentioning, or a difference that shuffling the same days could easily have produced. It does not mean the tag does not matter.",
  };
}

/** One grouping as bars, in the order the server sent the buckets. */
export function bucketChartRows(pattern: GroupPattern, measure: string): BucketRow[] {
  const value = (bucket: GroupBucket): number | null =>
    measure === MEASURE_ENERGY ? bucket.energy_mean : bucket.valence_mean;

  return pattern.buckets.map((bucket) => ({
    id: bucket.id,
    label: bucket.label,
    mean: bucket.enough_data ? value(bucket) : null,
    entries: bucket.entries,
    enoughData: bucket.enough_data,
    named: bucket.id === pattern.lowest_id || bucket.id === pattern.highest_id,
  }));
}

/** A grouping as a table: every bucket, including the ones too small to name. */
export function bucketTable(pattern: GroupPattern): DataTable {
  const rows = pattern.buckets.map((bucket) => ({
    bucket: bucket.label,
    entries: bucket.entries,
    valence: formatPoints(bucket.valence_mean),
    energy: formatPoints(bucket.energy_mean),
    named:
      bucket.id === pattern.lowest_id
        ? "lowest"
        : bucket.id === pattern.highest_id
          ? "highest"
          : null,
  }));

  return {
    caption: `${pattern.measure_label} by ${pattern.kind === "time_of_day" ? "part of the day" : "day of the week"}`,
    columns: [
      { key: "bucket", label: "Bucket" },
      { key: "entries", label: "Check-ins", numeric: true },
      { key: "valence", label: "Average mood", numeric: true },
      { key: "energy", label: "Average energy", numeric: true },
      { key: "named", label: "Named in the summary" },
    ],
    rows,
    note: "Averages are shown for every bucket, including the ones with too few check-ins to be named in a sentence. A small bucket is a small sample, not a small truth.",
  };
}

/** The trend as a table. Deliberately no chart: see the comment in the view. */
export function trendTable(trend: Trend): DataTable {
  const rows = trend.measures.map((item) => ({
    measure: item.label,
    previous: formatPoints(item.previous_mean),
    recent: formatPoints(item.recent_mean),
    change: formatPoints(item.change),
    ci: formatInterval(item.ci_low, item.ci_high),
    direction: item.direction_label,
    entries: `${item.previous_entries} then ${item.recent_entries}`,
  }));

  return {
    caption: `The last ${trend.window_days} days compared with the ${trend.window_days} before them`,
    columns: [
      { key: "measure", label: "Measure" },
      { key: "previous", label: `Previous ${trend.window_days} days`, numeric: true },
      { key: "recent", label: `Last ${trend.window_days} days`, numeric: true },
      { key: "change", label: "Change (points)", numeric: true },
      { key: "ci", label: "95% interval for the change" },
      { key: "direction", label: "Direction" },
      { key: "entries", label: "Check-ins (previous, then recent)" },
    ],
    rows,
    note: "A change is only called a direction when both windows have enough check-ins and the interval for the change does not include zero. Otherwise the direction reads as not enough data, which is an answer, not a shrug.",
  };
}

/** Weeks as bars: mood and energy means, oldest week first. */
export function weeklyChartRows(weeks: readonly Week[]): WeekRow[] {
  return [...weeks]
    .sort((left, right) => (left.start < right.start ? -1 : left.start > right.start ? 1 : 0))
    .map((week) => ({
      start: week.start,
      label: week.label,
      valence: week.enough_data ? week.valence_mean : null,
      energy: week.enough_data ? week.energy_mean : null,
      entries: week.entries,
    }));
}

/** Weeks as a table, including the tags and emotions counted in each one. */
export function weeklyTable(weeks: readonly Week[]): DataTable {
  const counts = (items: readonly { label: string; count: number }[]): string | null => {
    const named = items.filter((item) => item.count > 0);
    return named.length === 0
      ? null
      : named.map((item) => `${item.label} (${item.count})`).join(", ");
  };

  return {
    caption: "Week by week, Monday to Sunday",
    columns: [
      { key: "week", label: "Week starting" },
      { key: "entries", label: "Check-ins", numeric: true },
      { key: "days", label: "Days with a check-in", numeric: true },
      { key: "valence", label: "Average mood", numeric: true },
      { key: "energy", label: "Average energy", numeric: true },
      { key: "factors", label: "Tags" },
      { key: "emotions", label: "Emotions" },
    ],
    rows: weeks.map((week) => ({
      week: week.label,
      entries: week.entries,
      days: week.days_with_check_in,
      valence: formatPoints(week.valence_mean),
      energy: formatPoints(week.energy_mean),
      factors: counts(week.factors),
      emotions: counts(week.emotions),
    })),
    note: "A week with no check-ins stays in the table with empty averages. Weeks you did not use are part of the picture too.",
  };
}
