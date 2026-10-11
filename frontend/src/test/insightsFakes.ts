/**
 * Fake transports and fixtures for the insights tests.
 *
 * The charts are pure renderers over the wire shapes, so the fixtures here are
 * deliberately *statistically meaningful*: one factor (`sleep`) with a real
 * effect, one with too few days to compare (`weather`), and a series with fewer
 * than seven days so the "not enough data" copy has something to be right about.
 */

import type { ApiClient } from "@/lib/api";

import type {
  ExportBundle,
  FactorAssociation,
  InsightsPatterns,
  RollingPoint,
  RollingWindow,
  TimePattern,
  WeeklySummary,
} from "@/features/insights/types";

export function rollingPoint(overrides: Partial<RollingPoint> = {}): RollingPoint {
  return { day: "2026-03-15", valence_avg: 3.0, energy_avg: 3.0, count: 1, ...overrides };
}

export function rollingWindow(overrides: Partial<RollingWindow> = {}): RollingWindow {
  return {
    window_days: 7,
    points: [rollingPoint()],
    trend: "flat",
    trend_note: "Your mood has been stable over this period.",
    ...overrides,
  };
}

export function factorAssociation(
  overrides: Partial<FactorAssociation> = {},
): FactorAssociation {
  return {
    factor: "sleep",
    with_factor_avg: 2.2,
    without_factor_avg: 3.4,
    with_factor_count: 9,
    without_factor_count: 21,
    effect_size: -0.9,
    ci_lower: -1.3,
    ci_upper: -0.5,
    p_value: 0.002,
    note:
      "On days you tagged sleep, your mood was 1.2 points lower on average " +
      "(n=9 vs 21). This is an association, not a causal link.",
    ...overrides,
  };
}

export function timePattern(overrides: Partial<TimePattern> = {}): TimePattern {
  return {
    bucket: "10",
    valence_avg: 3.1,
    energy_avg: 3.0,
    count: 6,
    note: "6 check-in(s) in this hour.",
    ...overrides,
  };
}

/** A 7-day series with a mild upward drift, so the chart has a line to draw. */
export function sevenDayWindow(overrides: Partial<RollingWindow> = {}): RollingWindow {
  const points: RollingPoint[] = Array.from({ length: 7 }, (_, i) => ({
    day: `2026-03-${String(9 + i).padStart(2, "0")}`,
    valence_avg: 2.0 + i * 0.4,
    energy_avg: 2.5 + i * 0.2,
    count: 1,
  }));
  return {
    window_days: 7,
    points,
    trend: "up",
    trend_note: "Your mood has been trending up over this period.",
    ...overrides,
  };
}

export function insightsPatterns(
  overrides: Partial<InsightsPatterns> = {},
): InsightsPatterns {
  return {
    rolling_7: sevenDayWindow(),
    rolling_30: rollingWindow({ window_days: 30, points: sevenDayWindow().points }),
    factor_associations: [
      factorAssociation(),
      factorAssociation({
        factor: "friends",
        with_factor_avg: 4.0,
        without_factor_avg: 3.2,
        with_factor_count: 8,
        without_factor_count: 22,
        effect_size: 0.8,
        ci_lower: 0.3,
        ci_upper: 1.3,
        p_value: 0.004,
        note:
          "On days you tagged friends, your mood was 0.8 points higher on average " +
          "(n=8 vs 22). This is an association, not a causal link.",
      }),
      factorAssociation({
        factor: "weather",
        with_factor_avg: 3.1,
        without_factor_avg: null,
        with_factor_count: 2,
        without_factor_count: 28,
        effect_size: null,
        ci_lower: null,
        ci_upper: null,
        p_value: null,
        note:
          "Not enough data to compare days with vs without weather " +
          "(need at least 5 days per group; have 2 with, 28 without).",
      }),
    ],
    hourly_patterns: Array.from({ length: 24 }, (_, h) =>
      timePattern({
        bucket: String(h).padStart(2, "0"),
        count: h === 10 ? 6 : 1,
        valence_avg: h === 10 ? 3.1 : null,
        energy_avg: h === 10 ? 3.0 : null,
        note:
          h === 10
            ? "6 check-in(s) in this hour."
            : "Only 1 check-in(s) in this hour — need 5 to show a pattern.",
      }),
    ),
    weekday_patterns: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((bucket) =>
      timePattern({
        bucket,
        count: bucket === "Mon" ? 7 : 2,
        valence_avg: bucket === "Mon" ? 2.4 : null,
        energy_avg: bucket === "Mon" ? 2.8 : null,
        note:
          bucket === "Mon"
            ? "7 check-in(s) on this weekday."
            : `Only 2 check-in(s) on ${bucket} — need 5 to show a pattern.`,
      }),
    ),
    timezone: "Asia/Kolkata",
    timezone_fallback_used: false,
    min_entries_for_trend: 7,
    min_entries_per_group: 5,
    ...overrides,
  };
}

export function weeklySummary(overrides: Partial<WeeklySummary> = {}): WeeklySummary {
  return {
    locale: "en",
    period_start: "2026-03-09",
    period_end: "2026-03-15",
    total_check_ins: 7,
    valence_avg: 3.2,
    energy_avg: 2.9,
    trend: "up",
    top_factors: [
      ["sleep", 5],
      ["friends", 3],
    ],
    text:
      "Your week in review Your mood trended up this week. Average mood: 3.2/5, " +
      "energy: 2.9/5 over 7 check-in(s). Most common tags: sleep (5), friends (3). " +
      "This is a pattern summary, not a diagnosis.",
    ...overrides,
  };
}

/** A weekly summary for somebody who has barely started: no data at all. */
export function emptyWeeklySummary(overrides: Partial<WeeklySummary> = {}): WeeklySummary {
  return weeklySummary({
    total_check_ins: 0,
    valence_avg: null,
    energy_avg: null,
    trend: "not_enough_data",
    top_factors: [],
    text: "No check-ins this week. Whenever you start, that's day one.",
    ...overrides,
  });
}

/** Patterns for somebody with three check-ins: every rule says "not enough". */
export function sparseInsightsPatterns(): InsightsPatterns {
  return insightsPatterns({
    rolling_7: rollingWindow({
      points: [
        rollingPoint({ day: "2026-03-13", valence_avg: 3.0, energy_avg: 2.0 }),
        rollingPoint({ day: "2026-03-14", valence_avg: 4.0, energy_avg: 3.0 }),
        rollingPoint({ day: "2026-03-15", valence_avg: 2.0, energy_avg: 2.0 }),
      ],
      trend: "not_enough_data",
      trend_note: "Need at least 7 days with check-ins to show a trend.",
    }),
    rolling_30: rollingWindow({
      window_days: 30,
      points: [],
      trend: "not_enough_data",
      trend_note: "Need at least 7 days with check-ins to show a trend.",
    }),
    factor_associations: ["sleep", "study", "work", "family", "friends", "health", "money", "weather"].map(
      (factor) =>
        factorAssociation({
          factor,
          with_factor_avg: null,
          without_factor_avg: 3.0,
          with_factor_count: 0,
          without_factor_count: 3,
          effect_size: null,
          ci_lower: null,
          ci_upper: null,
          p_value: null,
          note:
            `Not enough data to compare days with vs without ${factor} ` +
            "(need at least 5 days per group; have 0 with, 3 without).",
        }),
    ),
    hourly_patterns: Array.from({ length: 24 }, (_, h) =>
      timePattern({
        bucket: String(h).padStart(2, "0"),
        valence_avg: null,
        energy_avg: null,
        count: h === 9 ? 3 : 0,
        note:
          h === 9
            ? "Only 3 check-in(s) in this hour — need 5 to show a pattern."
            : "Only 0 check-in(s) in this hour — need 5 to show a pattern.",
      }),
    ),
    weekday_patterns: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((bucket) =>
      timePattern({
        bucket,
        valence_avg: null,
        energy_avg: null,
        count: 0,
        note: `Only 0 check-in(s) on ${bucket} — need 5 to show a pattern.`,
      }),
    ),
  });
}

export function exportBundle(overrides: Partial<ExportBundle> = {}): ExportBundle {
  return {
    entries_csv:
      "local_date,created_at_utc,valence,energy,emotions,factors,note\n" +
      "2026-03-15,2026-03-15T09:00:00Z,3,3,\"\",\"sleep\",",
    factor_associations_csv:
      "factor,with_factor_avg,without_factor_avg,with_factor_count,without_factor_count," +
      "effect_size,ci_lower,ci_upper,p_value,note\n" +
      "sleep,2.2,3.4,9,21,-0.9,-1.3,-0.5,0.002,\"On days you tagged sleep…\"",
    time_patterns_csv:
      "type,bucket,valence_avg,energy_avg,count,note\nhour,10,3.1,3.0,6,\"6 check-in(s) in this hour.\"",
    ...overrides,
  };
}

export interface InsightsFake {
  client: ApiClient;
  /** Locales the weekly endpoint was asked for, in order. */
  locales: string[];
  patterns: InsightsPatterns;
  weekly: WeeklySummary;
  export: ExportBundle;
  /** Paths requested, in order — used to prove which endpoints were called. */
  paths: string[];
}

/** A client answering the three insights endpoints, recording what was asked. */
export function makeInsightsFake(
  options: {
    patterns?: InsightsPatterns;
    weekly?: WeeklySummary | ((locale: string) => WeeklySummary);
    export?: ExportBundle;
    failPatterns?: Error;
    failWeekly?: Error;
    failExport?: Error;
  } = {},
): InsightsFake {
  const fake: InsightsFake = {
    client: {} as ApiClient,
    locales: [],
    patterns: options.patterns ?? insightsPatterns(),
    weekly: typeof options.weekly === "function" ? options.weekly("en") : (options.weekly ?? weeklySummary()),
    export: options.export ?? exportBundle(),
    paths: [],
  };

  const client = {
    get: async (path: string): Promise<unknown> => {
      fake.paths.push(path);
      if (path.startsWith("/insights/patterns")) {
        if (options.failPatterns !== undefined) {
          throw options.failPatterns;
        }
        return fake.patterns;
      }
      if (path.startsWith("/insights/weekly")) {
        if (options.failWeekly !== undefined) {
          throw options.failWeekly;
        }
        const locale = new URL(path, "http://testserver").searchParams.get("locale") ?? "en";
        fake.locales.push(locale);
        if (typeof options.weekly === "function") {
          return options.weekly(locale);
        }
        return fake.weekly;
      }
      if (path.startsWith("/insights/export")) {
        if (options.failExport !== undefined) {
          throw options.failExport;
        }
        return fake.export;
      }
      throw new Error(`unexpected GET ${path}`);
    },
  };

  fake.client = client as unknown as ApiClient;
  return fake;
}
