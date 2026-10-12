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

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, isNetworkFailure, type ApiClient } from "@/lib/api";
import { loadOnboardingRecord } from "@/onboarding/storage";
import { isOnline } from "@/pwa/useOnlineStatus";

import { createMoodCheckIn, deleteMoodEntry, fetchMoodEntries, fetchMoodStreak } from "./endpoints";
import {
  MOOD_OUTBOX_KEY,
  enqueueCheckIn,
  loadOutbox,
  subscribeOutbox,
  type QueuedCheckIn,
} from "./outbox";
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

/**
 * What happened to a check-in (Day 25): stored on the server, or queued on this
 * device because the network was not there. Both are a success for the person
 * — their check-in is safe — and the UI says which, in words.
 */
export type MoodSubmitResult =
  { kind: "saved"; response: MoodCheckInResponse } | { kind: "queued"; item: QueuedCheckIn };

function newClientId(): string {
  return crypto.randomUUID();
}

/**
 * Save one check-in, then refresh everything it could have changed.
 *
 * Offline (or when the request cannot reach the server) the check-in goes into
 * the outbox instead of failing; `OutboxSync` sends it when the connection
 * returns. Every send carries a `client_id`, so a response lost on a flaky
 * network can be retried without storing the check-in twice. `networkMode:
 * "always"` matters: TanStack Query would otherwise *pause* the mutation while
 * offline and the person would watch a spinner instead of a confirmation.
 */
export function useMoodCheckIn(client: ApiClient = api) {
  const queryClient = useQueryClient();
  return useMutation<MoodSubmitResult, Error, MoodCheckInPayload>({
    networkMode: "always",
    mutationFn: async (payload) => {
      const clientId = newClientId();
      const recordedAt = new Date().toISOString();
      const userId = loadOnboardingRecord()?.user.id ?? null;
      const queue = (): MoodSubmitResult => ({
        kind: "queued",
        item: enqueueCheckIn(payload, { clientId, recordedAt, userId }),
      });
      if (!isOnline()) {
        return queue();
      }
      try {
        // Online, the server's clock dates the check-in; only the key is sent.
        const response = await createMoodCheckIn({ ...payload, client_id: clientId }, client);
        return { kind: "saved", response };
      } catch (error) {
        if (isNetworkFailure(error)) {
          // The request may even have arrived; the same client id makes the
          // later retry a replay rather than a duplicate.
          return queue();
        }
        throw error;
      }
    },
    onSuccess: async (result) => {
      if (result.kind !== "saved") {
        return;
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: moodKeys.history }),
        queryClient.invalidateQueries({ queryKey: moodKeys.streak }),
      ]);
    },
  });
}

let outboxSnapshotRaw: string | null | undefined;
let outboxSnapshot: QueuedCheckIn[] = [];

function readOutboxSnapshot(): QueuedCheckIn[] {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(MOOD_OUTBOX_KEY);
  } catch {
    raw = null;
  }
  // useSyncExternalStore needs a stable reference while nothing changed.
  if (raw !== outboxSnapshotRaw) {
    outboxSnapshotRaw = raw;
    outboxSnapshot = loadOutbox();
  }
  return outboxSnapshot;
}

const EMPTY_OUTBOX: QueuedCheckIn[] = [];

/** The check-ins waiting on this device, live across components and tabs. */
export function useMoodOutbox(): QueuedCheckIn[] {
  return useSyncExternalStore(subscribeOutbox, readOutboxSnapshot, () => EMPTY_OUTBOX);
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
