/**
 * The hooks the mood page runs on: the check-in mutation, the history, the
 * streak, and the local reminder preference.
 *
 * Housekeeping kept in one place so the components stay presentational:
 *
 * - a saved check-in invalidates both the history and the streak, so the numbers
 *   on screen can never be a render behind what was just written;
 * - a deleted check-in does the same — deleting a day changes the streak, and
 *   showing a stale one would look like the app had ignored the deletion;
 * - the reminder preference emits a window event, because it lives in
 *   `localStorage` and two components can change it (see `useMoodHint` for the
 *   same pattern, written on Day 12).
 */

import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type ApiClient } from "@/lib/api";

import { createMoodCheckIn, deleteMoodEntry, fetchMoodEntries, fetchMoodStreak } from "./endpoints";
import { loadMoodReminder, saveMoodReminder, type MoodReminder } from "./storage";
import type {
  MoodCheckInPayload,
  MoodCheckInResponse,
  MoodListResponse,
  MoodStreak,
} from "./types";

export const MOOD_MEMORY_EVENT = "manovia:mood-reminder";

export const moodKeys = {
  history: ["mood", "history"] as const,
  streak: ["mood", "streak"] as const,
};

const HISTORY_LIMIT = 30;

/** Save one check-in, then refresh everything it could have changed. */
export function useMoodCheckIn(client: ApiClient = api) {
  const queryClient = useQueryClient();
  return useMutation<MoodCheckInResponse, Error, MoodCheckInPayload>({
    mutationFn: (payload) => createMoodCheckIn(payload, client),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: moodKeys.history }),
        queryClient.invalidateQueries({ queryKey: moodKeys.streak }),
      ]);
    },
  });
}

/** The newest check-ins. */
export function useMoodHistory(client: ApiClient = api, limit = HISTORY_LIMIT) {
  return useQuery<MoodListResponse, Error>({
    queryKey: [...moodKeys.history, limit],
    queryFn: () => fetchMoodEntries({ limit }, client),
  });
}

export function useMoodStreak(client: ApiClient = api) {
  return useQuery<MoodStreak, Error>({
    queryKey: moodKeys.streak,
    queryFn: () => fetchMoodStreak(client),
  });
}

export function useDeleteMoodEntry(client: ApiClient = api) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteMoodEntry(id, client),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: moodKeys.history }),
        queryClient.invalidateQueries({ queryKey: moodKeys.streak }),
      ]);
    },
  });
}

/** The reminder preference, synced across components in this tab and others. */
export function useMoodReminder(): [MoodReminder, (next: MoodReminder) => void] {
  const [reminder, setReminder] = useState<MoodReminder>(() => loadMoodReminder());

  useEffect(() => {
    const sync = (): void => {
      setReminder(loadMoodReminder());
    };
    window.addEventListener(MOOD_MEMORY_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(MOOD_MEMORY_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const set = useCallback((next: MoodReminder): void => {
    saveMoodReminder(next);
    setReminder(next);
    window.dispatchEvent(new Event(MOOD_MEMORY_EVENT));
  }, []);

  return [reminder, set];
}
