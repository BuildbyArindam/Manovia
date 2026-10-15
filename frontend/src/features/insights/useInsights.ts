/**
 * The insights page's queries: pattern and weekly reports, plus a settings-gated
 * Mood Outlook request. These are plain `useQuery` calls over the endpoints in
 * `endpoints.ts`, with two settings that differ from the app's defaults on purpose:
 *
 * - `staleTime: 0` and `refetchOnMount: "always"`. The app caches for five
 *   minutes, which is right for exercises and wrong here: somebody can check in
 *   on the mood page and then walk over to this one, and an insight that ignores
 *   the check-in they just made is an insight that contradicts the page they
 *   came from. Re-reading costs one request and a couple of hundred
 *   milliseconds of server arithmetic; the alternative costs trust.
 * - no extra `retry`. The shared client already refuses to retry a 4xx, which
 *   is what a missing consent or an unknown timezone produces.
 *
 * The query key carries every parameter that changes the answer, so switching
 * the look-back window cannot serve the numbers for the old one.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { fetchPlanSettings, planSettingsKeys } from "@/features/plan/endpoints";
import type { PlanSettings } from "@/features/plan/types";
import { api, type ApiClient } from "@/lib/api";
import { deviceTimezone } from "@/lib/timezone";

import {
  fetchMoodOutlook,
  fetchPatternInsights,
  fetchWeeklyInsights,
  type InsightsQuery,
} from "./endpoints";
import type { MoodOutlook, PatternInsights, WeeklyInsights } from "./types";

export const insightsKeys = {
  patterns: ["insights", "patterns"] as const,
  weekly: ["insights", "weekly"] as const,
  outlook: ["insights", "outlook"] as const,
};

/** How far back `/patterns` looks: the server's own default window. */
export const PATTERNS_WINDOW_DAYS = 90;

/** How many weeks `/weekly` returns. Eight is two months of Mondays. */
export const WEEKLY_WEEKS = 8;

const FRESH = {
  staleTime: 0,
  refetchOnMount: "always" as const,
};

/** Rolling averages, tag comparisons, and the time-of-day and weekday patterns. */
export function usePatternInsights(
  client: ApiClient = api,
  query: InsightsQuery = {},
): ReturnType<typeof useQuery<PatternInsights, Error>> {
  const days = query.days ?? PATTERNS_WINDOW_DAYS;
  return useQuery<PatternInsights, Error>({
    queryKey: [...insightsKeys.patterns, days, query.locale ?? "account"],
    queryFn: () => fetchPatternInsights({ ...query, days }, client),
    ...FRESH,
  });
}

/** Week by week, with the trend across the most recent weeks. */
export function useWeeklyInsights(
  client: ApiClient = api,
  query: InsightsQuery = {},
): ReturnType<typeof useQuery<WeeklyInsights, Error>> {
  const weeks = query.weeks ?? WEEKLY_WEEKS;
  return useQuery<WeeklyInsights, Error>({
    queryKey: [...insightsKeys.weekly, weeks, query.locale ?? "account"],
    queryFn: () => fetchWeeklyInsights({ ...query, weeks }, client),
    ...FRESH,
  });
}

/**
 * Read settings first so an opted-out account never asks the Outlook endpoint
 * for a personal estimate. The backend enforces the same gate independently.
 */
export function useMoodOutlook(
  client: ApiClient = api,
  query: InsightsQuery = {},
): {
  settingsQuery: UseQueryResult<PlanSettings, Error>;
  outlookQuery: UseQueryResult<MoodOutlook, Error>;
} {
  const settingsQuery = useQuery<PlanSettings, Error>({
    queryKey: planSettingsKeys.current,
    queryFn: () => fetchPlanSettings(client),
    ...FRESH,
  });
  const timezone = query.tz ?? deviceTimezone();
  const outlookQuery = useQuery<MoodOutlook, Error>({
    queryKey: [...insightsKeys.outlook, timezone, query.locale ?? "account"],
    queryFn: () => fetchMoodOutlook({ ...query, tz: timezone }, client),
    enabled: settingsQuery.data?.mood_outlook_enabled === true,
    ...FRESH,
  });
  return { settingsQuery, outlookQuery };
}
