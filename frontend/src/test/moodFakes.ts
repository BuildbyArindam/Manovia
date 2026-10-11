/**
 * Fake transports for the mood tests.
 *
 * The endpoints take a client as their last argument (the same seam the chat
 * feature has), so tests can drive the real components — real state, real
 * keyboard handling, real query invalidation — without a network or a token
 * store. The HTTP layer itself is covered by the backend integration tests and by
 * the Playwright spec, which runs against a real server.
 */

import type { ApiClient } from "@/lib/api";

import type {
  MoodCheckInPayload,
  MoodCheckInResponse,
  MoodDeleteResponse,
  MoodEntry,
  MoodListResponse,
  MoodStreak,
} from "@/features/mood/types";

export function moodEntry(overrides: Partial<MoodEntry> = {}): MoodEntry {
  return {
    id: "entry-1",
    created_at: "2026-03-15T09:00:00Z",
    local_date: "2026-03-15",
    valence: 3,
    energy: 3,
    emotions: [],
    factors: [],
    note: null,
    timezone: "Asia/Kolkata",
    ...overrides,
  };
}

export function moodStreak(overrides: Partial<MoodStreak> = {}): MoodStreak {
  return {
    state: "today",
    timezone: "Asia/Kolkata",
    timezone_fallback_used: false,
    today: "2026-03-15",
    current_streak: 1,
    longest_streak: 1,
    checked_in_today: true,
    last_check_in: "2026-03-15",
    days_since_last_check_in: 0,
    total_check_ins: 1,
    freezes_used: 0,
    freeze_available: true,
    ...overrides,
  };
}

export interface MoodFake {
  client: ApiClient;
  /** Every payload the form actually sent, in order. */
  sent: MoodCheckInPayload[];
  /** Ids passed to DELETE, in order. */
  deleted: string[];
  checkInResponse: MoodCheckInResponse | null;
  history: MoodEntry[];
  failCheckIn: Error | null;
}

/**
 * Build a client that answers the mood endpoints.
 *
 * `history` is mutated as entries are created and deleted, so a test can assert
 * that the list the user sees reflects what they did — the invalidation path, not
 * just the response.
 */
export function makeMoodFake(
  options: {
    streak?: MoodStreak;
    history?: MoodEntry[];
    crisis?: MoodCheckInResponse["crisis"];
    failCheckIn?: Error;
  } = {},
): MoodFake {
  const fake: MoodFake = {
    client: {} as ApiClient,
    sent: [],
    deleted: [],
    checkInResponse: null,
    history: options.history ?? [],
    failCheckIn: options.failCheckIn ?? null,
  };

  const client = {
    get: async (path: string): Promise<unknown> => {
      if (path.startsWith("/mood/streak")) {
        return options.streak ?? moodStreak({ total_check_ins: fake.history.length });
      }
      if (path.startsWith("/mood")) {
        const response: MoodListResponse = {
          entries: [...fake.history].reverse(),
          count: fake.history.length,
          from: null,
          to: null,
          timezone: "Asia/Kolkata",
          timezone_fallback_used: false,
          limit: 30,
        };
        return response;
      }
      throw new Error(`unexpected GET ${path}`);
    },
    post: async (path: string, body?: unknown): Promise<unknown> => {
      if (path !== "/mood") {
        throw new Error(`unexpected POST ${path}`);
      }
      if (fake.failCheckIn !== null) {
        throw fake.failCheckIn;
      }
      const payload = body as MoodCheckInPayload & { tz?: string };
      fake.sent.push(payload);
      const entry = moodEntry({
        id: `entry-${fake.history.length + 1}`,
        valence: payload.valence,
        energy: payload.energy,
        emotions: payload.emotions,
        factors: payload.factors,
        note: payload.note ?? null,
      });
      fake.history.push(entry);
      const response: MoodCheckInResponse = {
        entry,
        streak: options.streak ?? moodStreak({ total_check_ins: fake.history.length }),
        crisis: options.crisis ?? null,
      };
      fake.checkInResponse = response;
      return response;
    },
    request: async (path: string, init: { method: string }): Promise<unknown> => {
      const id = path.replace("/mood/", "").split("?")[0] ?? "";
      if (init.method !== "DELETE") {
        throw new Error(`unexpected ${init.method} ${path}`);
      }
      fake.deleted.push(id);
      fake.history = fake.history.filter((entry) => entry.id !== id);
      const response: MoodDeleteResponse = {
        deleted: true,
        id,
        remaining_streak: moodStreak({ total_check_ins: fake.history.length }),
      };
      return response;
    },
  };

  fake.client = client as unknown as ApiClient;
  return fake;
}
