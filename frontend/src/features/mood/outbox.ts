/**
 * Mood check-ins made offline wait here until they can be sent (Day 25).
 *
 * The shape of the problem: somebody checks in on a train with no signal. The
 * check-in must not be lost, must count for the moment they made it, and must
 * never be stored twice — even though "send it when the connection returns" is
 * exactly the situation in which a response gets lost and a request gets
 * retried. So every queued check-in carries:
 *
 * * a `clientId` (random UUID) the server treats as an idempotency key — a
 *   retry, or two tabs syncing at once, stores one row (`POST /mood` answers
 *   the repeat with `replayed: true`);
 * * a `recordedAt` timestamp the server accepts up to 7 days back, so the
 *   check-in is filed under the day it was made, not the day it synced;
 * * the `userId` it was made under, so a check-in can never be sent as
 *   somebody else after a sign-out and sign-in on a shared phone.
 *
 * Storage is `localStorage`: a handful of small records, readable
 * synchronously, and the same place the session already lives. The note is
 * stored as typed until it is sent — the privacy trade-off is written down in
 * ADR 0025 and in the queued confirmation copy — and the queue is cleared on
 * sign-out and when onboarding is restarted.
 */

import { isApiError, isNetworkFailure, type ApiClient } from "@/lib/api";

import { createMoodCheckIn } from "./endpoints";
import type { MoodCheckInPayload, MoodCheckInResponse } from "./types";

export const MOOD_OUTBOX_KEY = "manovia.mood.outbox.v1";
export const MOOD_OUTBOX_EVENT = "manovia:mood-outbox";
/** The server refuses `recorded_at` older than this; so does the queue. */
export const OUTBOX_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
/** A runaway queue is a bug, not a diary: cap it. */
export const OUTBOX_MAX_ITEMS = 50;

export type OutboxStatus = "pending" | "failed";

/** Why a queued check-in could not be sent. Codes only — never the note. */
export type OutboxFailure =
  | "too_old"
  | "time_rejected"
  | "account_changed"
  | "rejected"
  | "consent_required"
  | "session_expired";

export interface QueuedCheckIn {
  clientId: string;
  /** ISO-8601 with offset: when the person checked in. */
  recordedAt: string;
  payload: MoodCheckInPayload;
  /** The account it was made under; `null` when unknown. */
  userId: string | null;
  attempts: number;
  status: OutboxStatus;
  failure: OutboxFailure | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isPayload(value: unknown): value is MoodCheckInPayload {
  return (
    isRecord(value) &&
    typeof value.valence === "number" &&
    typeof value.energy === "number" &&
    Array.isArray(value.emotions) &&
    Array.isArray(value.factors)
  );
}

function isQueued(value: unknown): value is QueuedCheckIn {
  return (
    isRecord(value) &&
    typeof value.clientId === "string" &&
    typeof value.recordedAt === "string" &&
    isPayload(value.payload) &&
    (value.userId === null || typeof value.userId === "string") &&
    typeof value.attempts === "number" &&
    (value.status === "pending" || value.status === "failed")
  );
}

/** Everything queued, oldest first. A malformed record is dropped, not fatal. */
export function loadOutbox(): QueuedCheckIn[] {
  try {
    const raw = window.localStorage.getItem(MOOD_OUTBOX_KEY);
    if (raw === null) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isQueued) : [];
  } catch {
    return [];
  }
}

function saveOutbox(items: readonly QueuedCheckIn[]): void {
  try {
    if (items.length === 0) {
      window.localStorage.removeItem(MOOD_OUTBOX_KEY);
    } else {
      window.localStorage.setItem(MOOD_OUTBOX_KEY, JSON.stringify(items));
    }
  } catch {
    // Storage full or blocked: the queue lives for this page only. The queued
    // confirmation still tells the truth about where the check-in is.
  }
  window.dispatchEvent(new Event(MOOD_OUTBOX_EVENT));
}

export interface EnqueueOptions {
  clientId: string;
  recordedAt: string;
  userId: string | null;
}

/** Add one check-in to the queue and return the stored record. */
export function enqueueCheckIn(
  payload: MoodCheckInPayload,
  options: EnqueueOptions,
): QueuedCheckIn {
  const item: QueuedCheckIn = {
    clientId: options.clientId,
    recordedAt: options.recordedAt,
    payload: {
      valence: payload.valence,
      energy: payload.energy,
      emotions: [...payload.emotions],
      factors: [...payload.factors],
      note: payload.note ?? null,
    },
    userId: options.userId,
    attempts: 0,
    status: "pending",
    failure: null,
  };
  const existing = loadOutbox().filter((queued) => queued.clientId !== item.clientId);
  // Oldest first; past the cap the *oldest* go, since the server would refuse
  // them first anyway (the 7-day window).
  saveOutbox([...existing, item].slice(-OUTBOX_MAX_ITEMS));
  return item;
}

export function removeFromOutbox(clientId: string): void {
  saveOutbox(loadOutbox().filter((item) => item.clientId !== clientId));
}

export function clearOutbox(): void {
  saveOutbox([]);
}

function update(clientId: string, change: Partial<QueuedCheckIn>): void {
  saveOutbox(
    loadOutbox().map((item) => (item.clientId === clientId ? { ...item, ...change } : item)),
  );
}

/** Subscribe to queue changes in this tab and others. */
export function subscribeOutbox(listener: () => void): () => void {
  const onStorage = (event: StorageEvent): void => {
    if (event.key === null || event.key === MOOD_OUTBOX_KEY) {
      listener();
    }
  };
  window.addEventListener(MOOD_OUTBOX_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(MOOD_OUTBOX_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}

export interface SyncResult {
  /** Check-ins the server now holds (new or replayed), in queue order. */
  sent: Array<{ item: QueuedCheckIn; response: MoodCheckInResponse }>;
  /** Check-ins that can never be sent as they are; kept for the person to see. */
  failed: QueuedCheckIn[];
  /** Still waiting (offline, or the server was temporarily unavailable). */
  waiting: number;
  /** True when the run stopped early because the network or server was down. */
  interrupted: boolean;
}

export interface SyncOptions {
  client?: ApiClient;
  /** The account signed in now; check-ins made under another are not sent. */
  userId: string | null;
  now?: number;
}

function failureFor(error: unknown): OutboxFailure | "retry" {
  if (isNetworkFailure(error)) {
    return "retry";
  }
  if (isApiError(error)) {
    if (error.status >= 500 || error.status === 429 || error.status === 408) {
      return "retry"; // includes 503 safety_unavailable: screen it later, never skip it
    }
    if (error.status === 401) {
      return "session_expired";
    }
    if (error.code === "consent_required") {
      return "consent_required";
    }
    if (error.code === "invalid_recorded_at") {
      return "time_rejected"; // older than 7 days, or this device's clock is far ahead
    }
    return "rejected";
  }
  return "retry";
}

let inFlight: Promise<SyncResult> | null = null;

/**
 * Send what is waiting, oldest first. One run at a time per tab; a second call
 * while one is running gets the same promise. Stops at the first failure that
 * is about the connection, because every later send would fail the same way.
 */
export function syncOutbox(options: SyncOptions): Promise<SyncResult> {
  if (inFlight !== null) {
    return inFlight;
  }
  inFlight = runSync(options).finally(() => {
    inFlight = null;
  });
  return inFlight;
}

async function runSync({ client, userId, now = Date.now() }: SyncOptions): Promise<SyncResult> {
  const result: SyncResult = { sent: [], failed: [], waiting: 0, interrupted: false };

  for (const item of loadOutbox()) {
    if (item.status === "failed") {
      result.failed.push(item);
      continue;
    }
    if (result.interrupted) {
      result.waiting += 1;
      continue;
    }
    if (item.userId !== null && userId !== null && item.userId !== userId) {
      update(item.clientId, { status: "failed", failure: "account_changed" });
      result.failed.push({ ...item, status: "failed", failure: "account_changed" });
      continue;
    }
    if (now - Date.parse(item.recordedAt) > OUTBOX_MAX_AGE_MS) {
      update(item.clientId, { status: "failed", failure: "too_old" });
      result.failed.push({ ...item, status: "failed", failure: "too_old" });
      continue;
    }

    try {
      const response = await createMoodCheckIn(
        { ...item.payload, client_id: item.clientId, recorded_at: item.recordedAt },
        client,
      );
      removeFromOutbox(item.clientId);
      result.sent.push({ item, response });
    } catch (error) {
      const failure = failureFor(error);
      if (failure === "retry") {
        update(item.clientId, { attempts: item.attempts + 1 });
        result.interrupted = true;
        result.waiting += 1;
      } else {
        update(item.clientId, { status: "failed", failure, attempts: item.attempts + 1 });
        result.failed.push({ ...item, status: "failed", failure });
      }
    }
  }
  return result;
}

/** Test seam: forget an in-flight run. */
export function __resetOutboxSyncForTests(): void {
  inFlight = null;
}
