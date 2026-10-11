/**
 * The insights wire shapes, mirroring `app/api/v1/insights.py`.
 *
 * One rule about these types, and it is the reason they exist: **every sentence
 * the reader sees is already written by the server, in their language, from a
 * reviewed template.** Nothing here is a hint to be re-phrased on the client,
 * and the fields that carry wording say so. A component that built its own
 * "your mood was X because Y" out of these numbers would be inventing copy that
 * nobody reviewed — which is the exact failure mode this feature is built to
 * avoid.
 *
 * `holdback` codes arrive as strings rather than as a union of literals because
 * the server owns them and may add one; the client maps the ones it knows and
 * falls back to the summary sentence for the rest.
 */

/** One day of a rolling-average series. `average` is null on a day with no data. */
export interface RollingPoint {
  /** ISO date (YYYY-MM-DD), in the reader's timezone. */
  day: string;
  average: number | null;
  entries: number;
  enough_data: boolean;
}

/** One rolling average (7- or 30-day) and the series a chart draws. */
export interface Rolling {
  measure: string;
  /** Localised bare-noun label, for an axis or a table header. */
  label: string;
  window_days: number;
  start: string;
  end: string;
  entries: number;
  average: number | null;
  enough_data: boolean;
  holdback: string | null;
  series: RollingPoint[];
}

/** One side of the trend comparison (the recent window or the one before it). */
export interface TrendWindow {
  start: string;
  end: string;
  entries: number;
  valence_mean: number | null;
  energy_mean: number | null;
}

export interface MeasureTrend {
  measure: string;
  label: string;
  direction: string;
  /** Localised short label for the direction: a badge, not a sentence. */
  direction_label: string;
  change: number | null;
  ci_low: number | null;
  ci_high: number | null;
  recent_mean: number | null;
  previous_mean: number | null;
  recent_entries: number;
  previous_entries: number;
  holdback: string | null;
}

export interface Trend {
  window_days: number;
  recent: TrendWindow;
  previous: TrendWindow;
  measures: MeasureTrend[];
  enough_data: boolean;
  holdback: string | null;
}

/** Days with one tag, compared with days without it. */
export interface Association {
  factor: string;
  /** Localised tag label. */
  label: string;
  measure: string;
  measure_label: string;
  tagged_n: number;
  untagged_n: number;
  tagged_mean: number | null;
  untagged_mean: number | null;
  difference: number | null;
  ci_low: number | null;
  ci_high: number | null;
  p_value: number | null;
  cohens_d: number | null;
  /** The corrected threshold this comparison was judged under. */
  family_alpha: number;
  reported: boolean;
  holdback: string | null;
  /**
   * The associative sentence, already localised — or null when the comparison is
   * not reported, because the words to overstate it must not exist in the
   * payload at all. Rendered verbatim.
   */
  sentence: string | null;
}

export interface FactorReport {
  comparisons: number;
  family_alpha: number;
  min_group_n: number;
  min_effect_points: number;
  associations: Association[];
}

/** One bucket of a grouping (a part of the day, or a weekday). */
export interface GroupBucket {
  id: string;
  /** Localised bare-noun label ("Morning", "Monday"). */
  label: string;
  entries: number;
  valence_mean: number | null;
  energy_mean: number | null;
  /** False when the bucket has fewer check-ins than the minimum group size. */
  enough_data: boolean;
}

export interface GroupPattern {
  kind: string;
  measure: string;
  measure_label: string;
  buckets: GroupBucket[];
  spread: number | null;
  lowest_id: string | null;
  highest_id: string | null;
  p_value: number | null;
  family_alpha: number;
  reported: boolean;
  holdback: string | null;
  /** The localised sentence, or the localised reason there is not one. */
  sentence: string;
}

/** The explainer: what was done, what it cannot say, and what it is not. */
export interface AboutInsights {
  title: string;
  /** The sentence that says these are associations and not a cause. */
  not_causal: string;
  limitations: string[];
  methods: string[];
  disclaimer: string;
  locale: string;
  locale_fallback_used: boolean;
}

export interface WeekCount {
  id: string;
  label: string;
  count: number;
}

/** One natural (Monday-Sunday) week. */
export interface Week {
  start: string;
  end: string;
  /** Localised spelling of `start`, for a heading. */
  label: string;
  entries: number;
  days_with_check_in: number;
  valence_mean: number | null;
  energy_mean: number | null;
  factors: WeekCount[];
  emotions: WeekCount[];
  enough_data: boolean;
}

/** What was looked at, so a reader can judge the answer before reading it. */
export interface InsightsMeta {
  generated_at: string;
  today: string;
  timezone: string;
  timezone_fallback_used: boolean;
  locale: string;
  locale_fallback_used: boolean;
  /** Check-ins in the window these numbers were made from. */
  considered_check_ins: number;
  /** Check-ins this account has in total (the window may be shorter). */
  total_check_ins: number;
  enough_data: boolean;
  /** The rules that produced this response, echoed rather than assumed. */
  min_entries_for_trend: number;
  scale_max: number;
}

/** `GET /api/v1/insights/weekly`. */
export interface WeeklyInsights extends InsightsMeta {
  weeks: number;
  weekly: Week[];
  trend: Trend;
  rolling: Rolling[];
  /** The localised summary, in reading order. */
  summary: string[];
  about: AboutInsights;
}

/** `GET /api/v1/insights/patterns`. */
export interface PatternInsights extends InsightsMeta {
  window_days: number;
  rolling: Rolling[];
  trend: Trend;
  factors: FactorReport;
  time_of_day: GroupPattern;
  weekday: GroupPattern;
  summary: string[];
  about: AboutInsights;
}
