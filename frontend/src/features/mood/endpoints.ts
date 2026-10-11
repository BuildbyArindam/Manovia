/**
 * The mood endpoints.
 *
 * The device timezone they send lives in `lib/timezone.ts` now, because insights
 * (Day 16) needs the same fact and a fact with two homes drifts. It is
 * re-exported here so this feature's callers — and their tests — did not move.
 */

import { api as defaultClient, type ApiClient } from "@/lib/api";
import { deviceTimezone } from "@/lib/timezone";

import type {
  MoodCheckInPayload,
  MoodCheckInResponse,
  MoodDeleteResponse,
  MoodListResponse,
  MoodStreak,
} from "./types";

export { deviceTimezone };

export const MOOD_ENDPOINTS = {
  checkIn: "/mood",
  list: "/mood",
  streak: "/mood/streak",
  entry: (id: string): string => `/mood/${id}`,
} as const;

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
  return client.get<MoodStreak>(
    `${MOOD_ENDPOINTS.streak}?tz=${encodeURIComponent(deviceTimezone())}`,
  );
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
