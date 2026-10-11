/**
 * The insights endpoints.
 *
 * Two routes, and the difference between them is the question, not the data:
 * `/weekly` answers "how did my weeks look", `/patterns` answers "is there
 * anything in when and with what I check in". Both send the device timezone for
 * the same reason the mood endpoints do — a "part of the day" pattern computed
 * in UTC would put somebody's evening check-ins in the afternoon.
 *
 * `locale` is sent only when the caller has one to send. Omitting it lets the
 * server use the account's own language, which is the behaviour a person would
 * expect and the one the backend tests pin.
 */

import { api as defaultClient, type ApiClient } from "@/lib/api";
import { deviceTimezone } from "@/lib/timezone";

import type { PatternInsights, WeeklyInsights } from "./types";

export const INSIGHTS_ENDPOINTS = {
  weekly: "/insights/weekly",
  patterns: "/insights/patterns",
} as const;

export interface InsightsQuery {
  /** IANA zone to count days in. Defaults to this device's own. */
  tz?: string;
  /** BCP-47 tag. Omitted means "whatever this account asked for". */
  locale?: string;
  /** Look-back for `/patterns` (the server rejects anything under 7). */
  days?: number;
  /** Number of weeks for `/weekly` (the server rejects anything over 12). */
  weeks?: number;
}

/** Build the query string, leaving out anything the caller did not choose. */
export function insightsQuery(query: InsightsQuery = {}): string {
  const params = new URLSearchParams({ tz: query.tz ?? deviceTimezone() });
  if (query.locale !== undefined) {
    params.set("locale", query.locale);
  }
  if (query.days !== undefined) {
    params.set("days", String(query.days));
  }
  if (query.weeks !== undefined) {
    params.set("weeks", String(query.weeks));
  }
  return params.toString();
}

/** Rolling averages, tag comparisons, and the parts-of-day and weekday patterns. */
export async function fetchPatternInsights(
  query: InsightsQuery = {},
  client: ApiClient = defaultClient,
): Promise<PatternInsights> {
  return client.get<PatternInsights>(`${INSIGHTS_ENDPOINTS.patterns}?${insightsQuery(query)}`);
}

/** Week by week, with the trend across the most recent weeks. */
export async function fetchWeeklyInsights(
  query: InsightsQuery = {},
  client: ApiClient = defaultClient,
): Promise<WeeklyInsights> {
  return client.get<WeeklyInsights>(`${INSIGHTS_ENDPOINTS.weekly}?${insightsQuery(query)}`);
}
