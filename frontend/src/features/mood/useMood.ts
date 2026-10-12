/**
 * The hooks the mood page runs on: the check-in mutation, the history, the
 * streak, the local reminder preference, and the Day 25 offline sync queue.
 *
 * Housekeeping kept in one place so the components stay presentational:
 *
 * - a saved check-in invalidates both the history and the streak, so the numbers
 *   on screen can never be a render behind what was just written;
 * - a check-in submitted while offline is written to the local queue
 *   (`offlineQueue.ts`) and replayed automatically on the `online` event;
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
import {
  MOOD_OFFLINE_QUEUE_EVENT,
  MOOD_OFFLINE_SYNCED_EVENT,
  enqueueOfflineMoodCheckIn,
  flushOfflineMoodQueue,
  isOfflineNetworkError,
  loadOfflineMoodQueue,
  queuedItemToMoodEntry,
  removeOfflineMoodCheckIn,
} from "./offlineQueue";
import { loadMoodReminder, saveMoodReminder, type MoodReminder } from "./storage";
import type {
  MoodCheckInPayload,
  MoodCheckInResponse,
  MoodEntry,
  MoodListResponse,
  MoodStreak,
} from "./types";

export const MOOD_MEMORY_EVENT = "manovia:mood-reminder";

export const moodKeys = {
  history: ["mood", "history"] as const,
  streak: ["mood", "streak"] as const,
};

const HISTORY_LIMIT = 30;

/** Save one check-in (or queue it locally when offline), then refresh state. */
export function useMoodCheckIn(client: ApiClient = api) {
  const queryClient = useQueryClient();
  return useMutation<MoodCheckInResponse, Error, MoodCheckInPayload>({
    mutationFn: async (payload) => {
      if (typeof navigator !== "undefined" && navigator.onLine === false) {
        return enqueueOfflineMoodCheckIn(payload);
      }
      try {
        return await createMoodCheckIn(payload, client);
      } catch (error) {
        if (isOfflineNetworkError(error)) {
          return enqueueOfflineMoodCheckIn(payload);
        }
        throw error;
      }
    },
    onSuccess: async (result) => {
      if (result.queued_offline) {
        return;
      }
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
    mutationFn: async (id: string) => {
      if (id.startsWith("offline-") && removeOfflineMoodCheckIn(id)) {
        return null;
      }
      return deleteMoodEntry(id, client);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: moodKeys.history }),
        queryClient.invalidateQueries({ queryKey: moodKeys.streak }),
      ]);
    },
  });
}

/**
 * Tracks locally queued offline mood check-ins and flushes them to the server
 * whenever connectivity returns.
 */
export function useOfflineMoodQueue(client: ApiClient = api): {
  queuedEntries: MoodEntry[];
  queuedCount: number;
  isSyncing: boolean;
  lastSyncedCount: number;
  syncNow: () => Promise<void>;
} {
  const queryClient = useQueryClient();
  const [queuedEntries, setQueuedEntries] = useState<MoodEntry[]>(() =>
    loadOfflineMoodQueue().map(queuedItemToMoodEntry),
  );
  const [isSyncing, setIsSyncing] = useState(false);
  const [lastSyncedCount, setLastSyncedCount] = useState(0);

  const refreshQueue = useCallback((): void => {
    setQueuedEntries(loadOfflineMoodQueue().map(queuedItemToMoodEntry));
  }, []);

  const syncNow = useCallback(async (): Promise<void> => {
    const current = loadOfflineMoodQueue();
    if (current.length === 0) {
      return;
    }
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      return;
    }
    setIsSyncing(true);
    try {
      const result = await flushOfflineMoodQueue(client);
      refreshQueue();
      if (result.syncedCount > 0) {
        setLastSyncedCount(result.syncedCount);
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: moodKeys.history }),
          queryClient.invalidateQueries({ queryKey: moodKeys.streak }),
        ]);
      }
    } finally {
      setIsSyncing(false);
    }
  }, [client, queryClient, refreshQueue]);

  useEffect(() => {
    const onSynced = (event: Event): void => {
      const detail = (event as CustomEvent<{ syncedCount?: number }>).detail;
      if (typeof detail?.syncedCount === "number" && detail.syncedCount > 0) {
        setLastSyncedCount(detail.syncedCount);
      }
      refreshQueue();
      void queryClient.invalidateQueries({ queryKey: moodKeys.history });
      void queryClient.invalidateQueries({ queryKey: moodKeys.streak });
    };
    const onOnline = (): void => {
      void syncNow();
    };
    const onSwMessage = (event: MessageEvent): void => {
      const data = event.data as { type?: string } | null;
      if (data?.type === "MANOVIA_FLUSH_MOOD_QUEUE") {
        void syncNow();
      }
    };

    window.addEventListener(MOOD_OFFLINE_QUEUE_EVENT, refreshQueue);
    window.addEventListener(MOOD_OFFLINE_SYNCED_EVENT, onSynced);
    window.addEventListener("storage", refreshQueue);
    window.addEventListener("online", onOnline);
    navigator.serviceWorker?.addEventListener("message", onSwMessage);

    // If the hook mounts while online and there are queued items from an
    // earlier offline session, flush immediately.
    if (
      (typeof navigator === "undefined" || navigator.onLine !== false) &&
      loadOfflineMoodQueue().length > 0
    ) {
      void syncNow();
    }

    return () => {
      window.removeEventListener(MOOD_OFFLINE_QUEUE_EVENT, refreshQueue);
      window.removeEventListener(MOOD_OFFLINE_SYNCED_EVENT, onSynced);
      window.removeEventListener("storage", refreshQueue);
      window.removeEventListener("online", onOnline);
      navigator.serviceWorker?.removeEventListener("message", onSwMessage);
    };
  }, [queryClient, refreshQueue, syncNow]);

  return {
    queuedEntries,
    queuedCount: queuedEntries.length,
    isSyncing,
    lastSyncedCount,
    syncNow,
  };
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
