/**
 * Offline queue for mood check-ins (Day 25).
 *
 * When a person checks in with no network connection, refusing the save or
 * throwing away their words is the wrong answer. Instead, the check-in is
 * recorded in `localStorage` (`manovia.mood.offlineQueue.v1`), shown immediately
 * in the confirmation card and recent history with an "Offline — waiting to
 * sync" badge, and replayed in FIFO order as soon as connectivity returns.
 *
 * A module-level lock (`flushInFlight`) ensures that a `window` `"online"`
 * event and a manual "Sync now" tap cannot replay the same queued item twice.
 */

import { type ApiClient, api as defaultClient, isApiError } from "@/lib/api";
import { readCachedCrisisResources } from "@/lib/pwa/offlineCache";
import { deviceTimezone } from "@/lib/timezone";

import { MOOD_ENDPOINTS } from "./endpoints";
import type {
  MoodCheckInPayload,
  MoodCheckInResponse,
  MoodCrisis,
  MoodEntry,
  MoodStreak,
} from "./types";

export const MOOD_OFFLINE_QUEUE_KEY = "manovia.mood.offlineQueue.v1";
export const MOOD_OFFLINE_QUEUE_EVENT = "manovia:mood-offline-queue";
export const MOOD_OFFLINE_SYNCED_EVENT = "manovia:mood-offline-synced";

export interface QueuedMoodCheckIn {
  id: string;
  created_at: string;
  local_date: string;
  timezone: string;
  payload: MoodCheckInPayload;
}

export interface MoodSyncResult {
  syncedCount: number;
  remainingCount: number;
  lastResponse: MoodCheckInResponse | null;
}

const OFFLINE_SELF_HARM_RE =
  /\b(kill myself|end my life|want to die|suicide|self[- ]harm|hurt myself)\b/i;

function todayInTimezone(tz: string, now: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(now);
    const year = parts.find((p) => p.type === "year")?.value;
    const month = parts.find((p) => p.type === "month")?.value;
    const day = parts.find((p) => p.type === "day")?.value;
    if (year && month && day) {
      return `${year}-${month}-${day}`;
    }
  } catch {
    // Fall back to UTC ISO date slice below.
  }
  return now.toISOString().slice(0, 10);
}

function isQueuedItem(value: unknown): value is QueuedMoodCheckIn {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  const payload = record.payload as Record<string, unknown> | undefined;
  return (
    typeof record.id === "string" &&
    typeof record.created_at === "string" &&
    typeof record.local_date === "string" &&
    typeof record.timezone === "string" &&
    typeof payload === "object" &&
    payload !== null &&
    typeof payload.valence === "number" &&
    typeof payload.energy === "number" &&
    Array.isArray(payload.emotions) &&
    Array.isArray(payload.factors)
  );
}

export function loadOfflineMoodQueue(
  storage: Storage = window.localStorage,
): QueuedMoodCheckIn[] {
  try {
    const raw = storage.getItem(MOOD_OFFLINE_QUEUE_KEY);
    if (raw === null) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter(isQueuedItem);
  } catch {
    return [];
  }
}

function saveOfflineMoodQueue(
  items: readonly QueuedMoodCheckIn[],
  storage: Storage = window.localStorage,
): void {
  try {
    if (items.length === 0) {
      storage.removeItem(MOOD_OFFLINE_QUEUE_KEY);
    } else {
      storage.setItem(MOOD_OFFLINE_QUEUE_KEY, JSON.stringify(items));
    }
  } catch {
    // Ignore storage quota errors in restricted mode.
  }
}

function notifyQueueChanged(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(MOOD_OFFLINE_QUEUE_EVENT));
  }
}

/** Convert a queued item into a `MoodEntry` for the confirmation and history UI. */
export function queuedItemToMoodEntry(item: QueuedMoodCheckIn): MoodEntry {
  return {
    id: item.id,
    created_at: item.created_at,
    local_date: item.local_date,
    valence: item.payload.valence,
    energy: item.payload.energy,
    emotions: [...item.payload.emotions],
    factors: [...item.payload.factors],
    note: item.payload.note ?? null,
    timezone: item.timezone,
    queued_offline: true,
  };
}

function buildOfflineCrisisIfNeeded(note: string | null | undefined): MoodCrisis | null {
  if (!note || !OFFLINE_SELF_HARM_RE.test(note)) {
    return null;
  }
  const cached = readCachedCrisisResources();
  return {
    level: "high",
    stored_level: "crisis",
    source: "offline_rules",
    matched_categories: ["self_harm_offline"],
    rationale_codes: ["offline.self_harm_keyword"],
    about_someone_else: false,
    locale: "en",
    region: cached?.region ?? "DEFAULT",
    message: {
      template_id: "crisis.offline.high",
      locale: "en",
      title: "You matter, and support is available right now",
      body: [
        "It sounds like you are going through something very heavy. Even while your device is offline, you can call or text the helplines below right away.",
      ],
      helpline_intro: "Free, confidential support:",
      emergency_instruction:
        "If you are in immediate danger, please call your local emergency number now.",
      trusted_person: "If you can, let someone you trust know you do not want to be alone right now.",
      safety_steps: [],
      closing: null,
      disclaimer: cached?.disclaimer ?? null,
    },
    emergency: cached?.emergency ?? null,
    resources: cached?.resources ?? [],
    check_in_saved: true,
  };
}

/**
 * Queue a check-in locally when the device is offline, and return a local
 * `MoodCheckInResponse` so the UI transitions to the confirmation card.
 */
export function enqueueOfflineMoodCheckIn(
  payload: MoodCheckInPayload,
  storage: Storage = window.localStorage,
): MoodCheckInResponse {
  const tz = deviceTimezone();
  const now = new Date();
  const localDate = todayInTimezone(tz, now);
  const existing = loadOfflineMoodQueue(storage);
  const id =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? `offline-${crypto.randomUUID()}`
      : `offline-${now.getTime()}-${existing.length + 1}`;

  const item: QueuedMoodCheckIn = {
    id,
    created_at: now.toISOString(),
    local_date: localDate,
    timezone: tz,
    payload: {
      valence: payload.valence,
      energy: payload.energy,
      emotions: [...payload.emotions],
      factors: [...payload.factors],
      note: payload.note ?? null,
    },
  };

  const next = [...existing, item];
  saveOfflineMoodQueue(next, storage);
  notifyQueueChanged();

  const streak: MoodStreak = {
    state: "today",
    timezone: tz,
    timezone_fallback_used: false,
    today: localDate,
    current_streak: 1,
    longest_streak: 1,
    checked_in_today: true,
    last_check_in: localDate,
    days_since_last_check_in: 0,
    total_check_ins: next.length,
    freezes_used: 0,
    freeze_available: true,
  };

  return {
    entry: queuedItemToMoodEntry(item),
    streak,
    crisis: buildOfflineCrisisIfNeeded(payload.note),
    queued_offline: true,
  };
}

export function removeOfflineMoodCheckIn(
  id: string,
  storage: Storage = window.localStorage,
): boolean {
  const existing = loadOfflineMoodQueue(storage);
  const filtered = existing.filter((item) => item.id !== id);
  if (filtered.length === existing.length) {
    return false;
  }
  saveOfflineMoodQueue(filtered, storage);
  notifyQueueChanged();
  return true;
}

/**
 * True when an error is a network-level failure (not an HTTP status returned by
 * the server) and the browser is offline.
 */
export function isOfflineNetworkError(error: unknown): boolean {
  if (isApiError(error)) {
    return false;
  }
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return true;
  }
  if (error instanceof TypeError && /fetch|network|failed/i.test(error.message)) {
    return true;
  }
  return false;
}

let flushInFlight: Promise<MoodSyncResult> | null = null;

/**
 * Replay every queued mood check-in to the backend in FIFO order.
 */
export async function flushOfflineMoodQueue(
  client: ApiClient = defaultClient,
  storage: Storage = window.localStorage,
): Promise<MoodSyncResult> {
  if (flushInFlight !== null) {
    return flushInFlight;
  }

  const run = async (): Promise<MoodSyncResult> => {
    const queue = loadOfflineMoodQueue(storage);
    if (queue.length === 0) {
      return { syncedCount: 0, remainingCount: 0, lastResponse: null };
    }
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      return { syncedCount: 0, remainingCount: queue.length, lastResponse: null };
    }

    let syncedCount = 0;
    let lastResponse: MoodCheckInResponse | null = null;
    const remaining: QueuedMoodCheckIn[] = [];

    for (let index = 0; index < queue.length; index += 1) {
      const item = queue[index];
      if (item === undefined) {
        continue;
      }
      try {
        const response = await client.post<MoodCheckInResponse>(MOOD_ENDPOINTS.checkIn, {
          ...item.payload,
          tz: item.timezone,
        });
        syncedCount += 1;
        lastResponse = response;
      } catch (error) {
        // If we lost connectivity mid-flush, keep the current and remaining items.
        if (isOfflineNetworkError(error) || (isApiError(error) && error.status >= 500)) {
          remaining.push(...queue.slice(index));
          break;
        }
        // A permanent 4xx validation error drops only the malformed item so it
        // cannot block the rest of the queue forever.
      }
    }

    saveOfflineMoodQueue(remaining, storage);
    notifyQueueChanged();

    if (syncedCount > 0 && typeof window !== "undefined") {
      window.dispatchEvent(
        new CustomEvent(MOOD_OFFLINE_SYNCED_EVENT, {
          detail: { syncedCount, remainingCount: remaining.length, lastResponse },
        }),
      );
    }

    return {
      syncedCount,
      remainingCount: remaining.length,
      lastResponse,
    };
  };

  flushInFlight = run().finally(() => {
    flushInFlight = null;
  });
  return flushInFlight;
}
