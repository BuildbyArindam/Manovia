/**
 * Fake insights payloads and a fake transport for the insights tests.
 *
 * The endpoints take a client as their last argument — the same seam the mood
 * and chat features use — so these tests drive the real components, real query
 * state and real keyboard handling without a network. The numbers here are
 * shaped like a real response: reported comparisons carry a sentence and an
 * interval, held-back ones carry a holdback code and no sentence at all, because
 * that absence is part of the contract the page has to survive.
 */

import type { ApiClient } from "@/lib/api";

import type {
  AboutInsights,
  Association,
  PatternInsights,
  Rolling,
  RollingPoint,
  Week,
  WeeklyInsights,
} from "@/features/insights/types";

const ABOUT: AboutInsights = {
  title: "About these insights",
  not_causal:
    "These are associations between two sets of your own days, not a cause. A tag says what was on your mind, not what it did to you.",
  limitations: [
    "A tag cannot tell us whether sleep was good or bad — only that you tagged it.",
    "Days you did not check in are missing, not neutral.",
    "Sixty check-ins is a small sample for eight tags.",
    "Averages hide the shape of a week.",
    "Nothing here is a diagnosis, and no number on this page is a score.",
    "Patterns describe the past window; they do not predict the next one.",
  ],
  methods: [
    "Rolling averages over 7 and 30 days, drawn only where a window has enough check-ins.",
    "Each tag compared with the days without it, with both sample sizes shown.",
    "Differences tested by shuffling the same check-ins between the two sets 2000 times.",
    "Ninety-five percent intervals from resampling your own check-ins.",
    "Sixteen comparisons, so each one is judged at p ≤ 0.003125 rather than 0.05.",
  ],
  disclaimer:
    "This is a reflective tool, not therapy and not medical advice. If things feel heavy, talking to a person helps more than any chart.",
  locale: "en",
  locale_fallback_used: false,
};

const META = {
  generated_at: "2026-10-11T06:00:00Z",
  today: "2026-10-11",
  timezone: "Asia/Kolkata",
  timezone_fallback_used: false,
  locale: "en",
  locale_fallback_used: false,
  considered_check_ins: 58,
  total_check_ins: 60,
  enough_data: true,
  min_entries_for_trend: 7,
  scale_max: 5,
} as const;

/** A rolling series with a gap in the middle, the way a real fortnight looks. */
function rollingSeries(
  measure: string,
  label: string,
  windowDays: number,
  base: number,
  withSeries = true,
): Rolling {
  const points: RollingPoint[] = [];
  const start = new Date(Date.UTC(2026, 7, 17));
  for (let index = 0; index < 56; index += 1) {
    const day = new Date(start.getTime() + index * 86_400_000);
    // Days 20-27 are the quiet fortnight: too few check-ins for a window.
    const gap = index >= 20 && index <= 27;
    points.push({
      day: day.toISOString().slice(0, 10),
      average: gap || index < windowDays - 1 ? null : Math.round((base + index / 90) * 100) / 100,
      entries: gap ? 0 : index < windowDays - 1 ? index + 1 : windowDays,
      enough_data: !gap && index >= windowDays - 1,
    });
  }
  return {
    measure,
    label,
    window_days: windowDays,
    start: "2026-08-17",
    end: "2026-10-11",
    entries: 48,
    average: base,
    enough_data: true,
    holdback: null,
    series: withSeries ? points : [],
  };
}

function association(overrides: Partial<Association>): Association {
  return {
    factor: "sleep",
    label: "sleep",
    measure: "valence",
    measure_label: "Mood",
    tagged_n: 24,
    untagged_n: 34,
    tagged_mean: 2.4,
    untagged_mean: 3.25,
    difference: -0.85,
    ci_low: -1.19,
    ci_high: -0.5,
    p_value: 0.0005,
    cohens_d: -1.18,
    family_alpha: 0.003125,
    reported: true,
    holdback: null,
    sentence:
      "On days you tagged sleep, your mood was about 0.85 points lower than on days without that tag (24 check-ins vs 34).",
    ...overrides,
  };
}

/** A payload with two reported tags, two held-back ones, and no grouping claim. */
export function patternInsights(overrides: Partial<PatternInsights> = {}): PatternInsights {
  const associations: Association[] = [
    association({}),
    association({
      factor: "friends",
      label: "friends",
      tagged_n: 21,
      untagged_n: 37,
      tagged_mean: 3.95,
      untagged_mean: 3.25,
      difference: 0.7,
      ci_low: 0.33,
      ci_high: 1.07,
      cohens_d: 0.93,
      p_value: 0.0025,
      sentence:
        "On days you tagged friends, your mood was about 0.7 points higher than on days without that tag (21 check-ins vs 37).",
    }),
    association({
      factor: "work",
      label: "work",
      tagged_n: 3,
      untagged_n: 55,
      tagged_mean: 2.7,
      untagged_mean: 3.2,
      difference: -0.5,
      ci_low: null,
      ci_high: null,
      p_value: null,
      cohens_d: null,
      reported: false,
      holdback: "group_too_small",
      sentence: null,
    }),
    association({
      factor: "study",
      label: "study",
      tagged_n: 18,
      untagged_n: 40,
      tagged_mean: 3.1,
      untagged_mean: 3.25,
      difference: -0.15,
      ci_low: -0.6,
      ci_high: 0.3,
      p_value: 0.42,
      cohens_d: -0.2,
      reported: false,
      holdback: "small_difference",
      sentence: null,
    }),
  ];

  return {
    ...META,
    window_days: 90,
    rolling: [
      rollingSeries("valence", "Mood", 7, 3.14),
      rollingSeries("valence", "Mood", 30, 3.03),
      rollingSeries("energy", "Energy", 7, 3.0),
      rollingSeries("energy", "Energy", 30, 3.07),
    ],
    trend: {
      window_days: 7,
      recent: {
        start: "2026-10-05",
        end: "2026-10-11",
        entries: 7,
        valence_mean: 3.4,
        energy_mean: 3.1,
      },
      previous: {
        start: "2026-09-28",
        end: "2026-10-04",
        entries: 7,
        valence_mean: 3,
        energy_mean: 3.2,
      },
      measures: [
        {
          measure: "valence",
          label: "Mood",
          direction: "steady",
          direction_label: "About the same",
          change: 0.4,
          ci_low: -0.2,
          ci_high: 1,
          recent_mean: 3.4,
          previous_mean: 3,
          recent_entries: 7,
          previous_entries: 7,
          holdback: "within_noise",
        },
        {
          measure: "energy",
          label: "Energy",
          direction: "not_enough_data",
          direction_label: "Not enough data yet",
          change: null,
          ci_low: null,
          ci_high: null,
          recent_mean: null,
          previous_mean: null,
          recent_entries: 3,
          previous_entries: 7,
          holdback: "not_enough_data",
        },
      ],
      enough_data: true,
      holdback: null,
    },
    factors: {
      comparisons: 16,
      family_alpha: 0.003125,
      min_group_n: 5,
      min_effect_points: 0.4,
      associations,
    },
    time_of_day: {
      kind: "time_of_day",
      measure: "valence",
      measure_label: "Mood",
      buckets: [
        {
          id: "morning",
          label: "Morning",
          entries: 18,
          valence_mean: 3.1,
          energy_mean: 3,
          enough_data: true,
        },
        {
          id: "afternoon",
          label: "Afternoon",
          entries: 14,
          valence_mean: 3.3,
          energy_mean: 2.9,
          enough_data: true,
        },
        {
          id: "evening",
          label: "Evening",
          entries: 22,
          valence_mean: 3.5,
          energy_mean: 3.2,
          enough_data: true,
        },
        {
          id: "night",
          label: "Night",
          entries: 4,
          valence_mean: 2.5,
          energy_mean: 2,
          enough_data: false,
        },
      ],
      spread: null,
      lowest_id: null,
      highest_id: null,
      p_value: 0.142,
      family_alpha: 0.0025,
      reported: false,
      holdback: "within_noise",
      sentence:
        "Your mood did not differ between parts of the day more than shuffling the same check-ins would produce.",
    },
    weekday: {
      kind: "weekday",
      measure: "valence",
      measure_label: "Mood",
      buckets: [
        {
          id: "monday",
          label: "Monday",
          entries: 9,
          valence_mean: 3,
          energy_mean: 2.8,
          enough_data: true,
        },
        {
          id: "tuesday",
          label: "Tuesday",
          entries: 8,
          valence_mean: 3.2,
          energy_mean: 3,
          enough_data: true,
        },
        {
          id: "wednesday",
          label: "Wednesday",
          entries: 9,
          valence_mean: 3.4,
          energy_mean: 3.1,
          enough_data: true,
        },
        {
          id: "thursday",
          label: "Thursday",
          entries: 8,
          valence_mean: 3.1,
          energy_mean: 3,
          enough_data: true,
        },
        {
          id: "friday",
          label: "Friday",
          entries: 9,
          valence_mean: 3.6,
          energy_mean: 3.4,
          enough_data: true,
        },
        {
          id: "saturday",
          label: "Saturday",
          entries: 8,
          valence_mean: 3.7,
          energy_mean: 3.5,
          enough_data: true,
        },
        {
          id: "sunday",
          label: "Sunday",
          entries: 7,
          valence_mean: 3.3,
          energy_mean: 3.2,
          enough_data: true,
        },
      ],
      spread: null,
      lowest_id: null,
      highest_id: null,
      p_value: 0.345,
      family_alpha: 0.0025,
      reported: false,
      holdback: "within_noise",
      sentence:
        "Your mood did not differ between days of the week more than shuffling the same check-ins would produce.",
    },
    summary: [
      "Over the last 7 days your mood was about the same as the 7 days before it (7 check-ins vs 7).",
      "On days you tagged sleep, your mood was about 0.85 points lower than on days without that tag (24 check-ins vs 34).",
      "On days you tagged friends, your mood was about 0.7 points higher than on days without that tag (21 check-ins vs 37).",
      "Your mood did not differ between parts of the day more than shuffling the same check-ins would produce.",
    ],
    about: ABOUT,
    ...overrides,
  };
}

function week(overrides: Partial<Week> = {}): Week {
  return {
    start: "2026-10-05",
    end: "2026-10-11",
    label: "5 October 2026",
    entries: 6,
    days_with_check_in: 5,
    valence_mean: 3.4,
    energy_mean: 3.1,
    factors: [
      { id: "sleep", label: "sleep", count: 3 },
      { id: "friends", label: "friends", count: 2 },
    ],
    emotions: [{ id: "calm", label: "calm", count: 4 }],
    enough_data: true,
    ...overrides,
  };
}

export function weeklyInsights(overrides: Partial<WeeklyInsights> = {}): WeeklyInsights {
  return {
    ...META,
    weeks: 4,
    weekly: [
      week({
        start: "2026-09-14",
        end: "2026-09-20",
        label: "14 September 2026",
        entries: 5,
        valence_mean: 3,
        energy_mean: 2.9,
      }),
      week({
        start: "2026-09-21",
        end: "2026-09-27",
        label: "21 September 2026",
        entries: 7,
        valence_mean: 3.2,
        energy_mean: 3,
      }),
      week({
        start: "2026-09-28",
        end: "2026-10-04",
        label: "28 September 2026",
        entries: 0,
        days_with_check_in: 0,
        valence_mean: null,
        energy_mean: null,
        factors: [],
        emotions: [],
        enough_data: false,
      }),
      week({}),
    ],
    trend: patternInsights().trend,
    rolling: [rollingSeries("valence", "Mood", 7, 3.14, false)],
    summary: [
      "Here are your last 4 weeks of check-ins: 18 in total.",
      "The week of 5 October 2026 had 6 check-ins across 5 days.",
      "One week in this range had no check-ins, and that is fine.",
    ],
    about: ABOUT,
    ...overrides,
  };
}

export interface InsightsFake {
  client: ApiClient;
  /** Every path the page asked for, in order. */
  calls: string[];
}

/**
 * A transport that answers the two insights routes from the payloads given.
 *
 * `failPatterns` / `failWeekly` make one of them throw, which is how the error
 * and consent states are tested without a server.
 */
export function makeInsightsFake(
  options: {
    patterns?: PatternInsights;
    weekly?: WeeklyInsights;
    failPatterns?: Error;
    failWeekly?: Error;
  } = {},
): InsightsFake {
  const fake: InsightsFake = { client: {} as ApiClient, calls: [] };

  const client = {
    get: async (path: string): Promise<unknown> => {
      fake.calls.push(path);
      if (path.startsWith("/insights/weekly")) {
        if (options.failWeekly !== undefined) {
          throw options.failWeekly;
        }
        return options.weekly ?? weeklyInsights();
      }
      if (path.startsWith("/insights/patterns")) {
        if (options.failPatterns !== undefined) {
          throw options.failPatterns;
        }
        return options.patterns ?? patternInsights();
      }
      throw new Error(`Unexpected path in the insights fake: ${path}`);
    },
  };

  fake.client = client as unknown as ApiClient;
  return fake;
}
