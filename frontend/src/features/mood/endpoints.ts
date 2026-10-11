/**
 * The mood endpoints, and the one piece of browser knowledge they need: the
 * device's own timezone.
 *
 * `Intl.DateTimeFormat().resolvedOptions().timeZone` is an IANA name
 * (`Asia/Kolkata`), which is exactly what the backend's streak arithmetic wants.
 * Sending it matters: without it the server counts days in UTC, and somebody
 * checking in at 00:30 in Delhi would see their streak disagree with their
 * calendar. It is a preference, not an identifier — the server stores no zone.
 */

import { api as defaultClient, type ApiClient } from "@/lib/api";

import type {
  MoodCheckInPayload,
  MoodCheckInResponse,
  MoodDeleteResponse,
  MoodListResponse,
  MoodStreak,
} from "./types";

export const MOOD_ENDPOINTS = {
  checkIn: "/mood",
  list: "/mood",
  streak: "/mood/streak",
  entry: (id: string): string => `/mood/${id}`,
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

/**
 * Save one check-in.
 *
 * `tz` always comes from the device rather than from a prop: a caller that forgot
 * to pass it would silently get UTC streaks.
 */
export async function createMoodCheckIn(
  payload: MoodCheckInPayload,
  client: ApiClient = defaultClient,
): Promise<MoodCheckInResponse> {
  return client.post<MoodCheckInResponse>(MOOD_ENDPOINTS.checkIn, {
    ...payload,
    tz: deviceTimezone(),
  });
}

/** Check-ins, newest first, optionally bounded by local days (`YYYY-MM-DD`). */
export async function fetchMoodEntries(
  options: { from?: string; to?: string; limit?: number } = {},
  client: ApiClient = defaultClient,
): Promise<MoodListResponse> {
  const params = new URLSearchParams({ tz: deviceTimezone() });
  if (options.from !== undefined) {
    params.set("from", options.from);
  }
  if (options.to !== undefined) {
    params.set("to", options.to);
  }
  if (options.limit !== undefined) {
    params.set("limit", String(options.limit));
  }
  return client.get<MoodListResponse>(`${MOOD_ENDPOINTS.list}?${params.toString()}`);
}

/** The streak, counted by the server in this device's timezone. */
export async function fetchMoodStreak(client: ApiClient = defaultClient): Promise<MoodStreak> {
  return client.get<MoodStreak>(`${MOOD_ENDPOINTS.streak}?tz=${encodeURIComponent(deviceTimezone())}`);
}

/** Delete one check-in. The response carries the streak that is left. */
export async function deleteMoodEntry(
  id: string,
  client: ApiClient = defaultClient,
): Promise<MoodDeleteResponse> {
  return client.request<MoodDeleteResponse>(
    `${MOOD_ENDPOINTS.entry(id)}?tz=${encodeURIComponent(deviceTimezone())}`,
    { method: "DELETE" },
  );
}
