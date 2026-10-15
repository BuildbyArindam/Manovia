/**
 * Fake transports for the daily-plan tests.
 *
 * Same seam as the mood fakes: every plan endpoint takes a client as its last
 * argument, so tests drive the real components — real state, real react-query
 * invalidation — without a network. The HTTP contract itself is covered by the
 * backend integration tests and the Playwright spec.
 */

import type { ApiClient } from "@/lib/api";

import type { MoodCheckInResponse } from "@/features/mood/types";
import type { PlanItem, PlanSettings, PlanTodayResponse } from "@/features/plan/types";
import { moodEntry, moodStreak } from "@/test/moodFakes";

export function planItem(overrides: Partial<PlanItem> = {}): PlanItem {
  return {
    id: "item-1",
    kind: "check_in",
    ref: null,
    title: "Take a first check-in",
    why: "There is no mood check-in yet, and ten seconds helps Manovia learn your rhythm.",
    status: "pending",
    created_at: "2026-10-11T09:00:00Z",
    ...overrides,
  };
}

export function planToday(overrides: Partial<PlanTodayResponse> = {}): PlanTodayResponse {
  return {
    date: "2026-10-11",
    timezone: "Asia/Kolkata",
    timezone_default_used: false,
    quiet_hours_active: false,
    support_only: false,
    items: [planItem()],
    count: 1,
    ...overrides,
  };
}

export function planSettings(overrides: Partial<PlanSettings> = {}): PlanSettings {
  return {
    preferred_exercise_tags: [],
    quiet_hours_enabled: false,
    quiet_start: "22:00",
    quiet_end: "07:00",
    mood_outlook_enabled: false,
    known_exercise_tags: ["breathing", "grounding", "mindfulness", "worry"],
    ...overrides,
  };
}

export interface PlanFake {
  client: ApiClient;
  /** Ids marked done, in order. */
  done: string[];
  /** Ids skipped, in order. */
  skipped: string[];
  /** The plan the fake serves, mutated as items are settled. */
  plan: PlanTodayResponse;
  /** Settings the fake serves, mutated by PUT. */
  settings: PlanSettings;
  /** When set, the next done/skip call fails with this error. */
  failSettle: Error | null;
  /** When set, the next PUT /settings fails with this error. */
  failSettings: Error | null;
  /** Mood check-ins sent from the quick form (the home page shares the fake). */
  moodSent: Array<{ valence: number; energy: number }>;
}

export function makePlanFake(
  options: {
    plan?: PlanTodayResponse;
    settings?: PlanSettings;
    failToday?: Error;
    failSettingsLoad?: Error;
  } = {},
): PlanFake {
  const fake: PlanFake = {
    client: {} as ApiClient,
    done: [],
    skipped: [],
    plan: options.plan ?? planToday(),
    settings: options.settings ?? planSettings(),
    failSettle: null,
    failSettings: null,
    moodSent: [],
  };
  const failToday = options.failToday ?? null;
  const failSettingsLoad = options.failSettingsLoad ?? null;

  function settle(id: string, status: PlanItem["status"]): PlanItem {
    if (fake.failSettle !== null) {
      const error = fake.failSettle;
      fake.failSettle = null;
      throw error;
    }
    const item = fake.plan.items.find((candidate) => candidate.id === id);
    if (item === undefined) {
      throw new Error(`unknown plan item ${id}`);
    }
    item.status = status;
    return item;
  }

  const client = {
    get: async (path: string): Promise<unknown> => {
      if (path.startsWith("/plan/today")) {
        if (failToday !== null) {
          throw failToday;
        }
        return fake.plan;
      }
      if (path.startsWith("/settings")) {
        if (failSettingsLoad !== null) {
          throw failSettingsLoad;
        }
        return fake.settings;
      }
      throw new Error(`unexpected GET ${path}`);
    },
    post: async (path: string, body?: unknown): Promise<unknown> => {
      if (path.startsWith("/mood")) {
        const payload = body as { valence: number; energy: number };
        fake.moodSent.push({ valence: payload.valence, energy: payload.energy });
        const response: MoodCheckInResponse = {
          entry: moodEntry({ valence: payload.valence, energy: payload.energy }),
          streak: moodStreak(),
          crisis: null,
        };
        return response;
      }
      const match = /^\/plan\/(?<id>[^/]+)\/(?<action>done|skip)$/.exec(path);
      if (match?.groups !== undefined) {
        const { id, action } = match.groups as { id: string; action: "done" | "skip" };
        if (action === "done") {
          fake.done.push(id);
          return settle(id, "done");
        }
        fake.skipped.push(id);
        return settle(id, "skipped");
      }
      throw new Error(`unexpected POST ${path}`);
    },
    request: async (path: string, init: { method?: string; body?: unknown }): Promise<unknown> => {
      if (path === "/settings" && init.method === "PUT") {
        if (fake.failSettings !== null) {
          const error = fake.failSettings;
          fake.failSettings = null;
          throw error;
        }
        const payload = init.body as PlanSettings;
        fake.settings = {
          ...fake.settings,
          preferred_exercise_tags: payload.preferred_exercise_tags,
          quiet_hours_enabled: payload.quiet_hours_enabled,
          quiet_start: payload.quiet_start,
          quiet_end: payload.quiet_end,
          mood_outlook_enabled: payload.mood_outlook_enabled,
        };
        return fake.settings;
      }
      throw new Error(`unexpected REQUEST ${init.method ?? "?"} ${path}`);
    },
  };

  fake.client = client as unknown as ApiClient;
  return fake;
}
