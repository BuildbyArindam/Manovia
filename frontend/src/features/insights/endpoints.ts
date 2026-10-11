/**
 * The insights endpoints.
 */

import { api as defaultClient, type ApiClient } from "@/lib/api";

import type { ExportBundle, InsightsPatterns, WeeklySummary } from "./types";

export const INSIGHTS_ENDPOINTS = {
  weekly: "/insights/weekly",
  patterns: "/insights/patterns",
  export: "/insights/export",
} as const;

/** The device's IANA timezone, or `"UTC"` when the browser will not say. */
export function deviceTimezone(): string {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof zone === "string" && zone.length > 0 ? zone : "UTC";
  } catch {
    return "UTC";
  }
}

/** Localised weekly summary for the last 7 days. */
export async function fetchWeeklySummary(
  options: { locale?: "en" | "hi" | "bn" } = {},
  client: ApiClient = defaultClient,
): Promise<WeeklySummary> {
  const params = new URLSearchParams({
    tz: deviceTimezone(),
    locale: options.locale ?? "en",
  });
  return client.get<WeeklySummary>(`${INSIGHTS_ENDPOINTS.weekly}?${params.toString()}`);
}

/** Rolling averages, factor associations, and time patterns. */
export async function fetchInsightsPatterns(
  client: ApiClient = defaultClient,
): Promise<InsightsPatterns> {
  const params = new URLSearchParams({ tz: deviceTimezone() });
  return client.get<InsightsPatterns>(`${INSIGHTS_ENDPOINTS.patterns}?${params.toString()}`);
}

/** Export all insights data as CSV. */
export async function exportInsights(client: ApiClient = defaultClient): Promise<ExportBundle> {
  const params = new URLSearchParams({ tz: deviceTimezone() });
  return client.get<ExportBundle>(`${INSIGHTS_ENDPOINTS.export}?${params.toString()}`);
}
