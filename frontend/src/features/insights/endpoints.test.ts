/**
 * The insights endpoints: which path, and which parameters.
 *
 * The parameters matter more than they look. `tz` decides what "evening" means,
 * `days` decides how far back a pattern is claimed over, and `locale` decides
 * whose language the sentences arrive in — and an omitted `locale` is a choice
 * (use the account's own language), not an oversight, so it must not be sent as
 * an empty string.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiClient } from "@/lib/api";
import { moodOutlook, patternInsights, weeklyInsights } from "@/test/insightsFakes";

import {
  INSIGHTS_ENDPOINTS,
  fetchMoodOutlook,
  fetchPatternInsights,
  fetchWeeklyInsights,
  insightsQuery,
} from "./endpoints";

beforeEach(() => {
  // The same stub the mood endpoint tests use: the device zone is an input to
  // every request this feature makes, so the tests say what it is rather than
  // inheriting whatever machine they happen to run on.
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
    timeZone: "Asia/Kolkata",
  } as Intl.ResolvedDateTimeFormatOptions);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function recordingClient(response: unknown): { client: ApiClient; calls: string[] } {
  const calls: string[] = [];
  const client = {
    get: async (path: string): Promise<unknown> => {
      calls.push(path);
      return response;
    },
  } as unknown as ApiClient;
  return { client, calls };
}

describe("insightsQuery", () => {
  it("always sends the device timezone", () => {
    expect(insightsQuery()).toBe("tz=Asia%2FKolkata");
  });

  it("falls back to UTC when the browser will not name a zone", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      timeZone: "",
    } as Intl.ResolvedDateTimeFormatOptions);

    expect(insightsQuery()).toBe("tz=UTC");
  });

  it("adds only the parameters the caller chose", () => {
    expect(insightsQuery({ locale: "hi", days: 30 })).toBe("tz=Asia%2FKolkata&locale=hi&days=30");
    expect(insightsQuery({ weeks: 8 })).toBe("tz=Asia%2FKolkata&weeks=8");
  });

  it("lets a caller override the timezone rather than trusting the device", () => {
    expect(insightsQuery({ tz: "UTC" })).toBe("tz=UTC");
  });
});

describe("fetchPatternInsights", () => {
  it("reads the patterns route with the window it was given", async () => {
    const { client, calls } = recordingClient(patternInsights());

    const result = await fetchPatternInsights({ days: 60, locale: "bn" }, client);

    expect(calls[0]).toBe(`${INSIGHTS_ENDPOINTS.patterns}?tz=Asia%2FKolkata&locale=bn&days=60`);
    expect(result.factors.comparisons).toBe(16);
  });
});

describe("fetchWeeklyInsights", () => {
  it("reads the weekly route with the number of weeks it was given", async () => {
    const { client, calls } = recordingClient(weeklyInsights());

    const result = await fetchWeeklyInsights({ weeks: 4 }, client);

    expect(calls[0]).toBe(`${INSIGHTS_ENDPOINTS.weekly}?tz=Asia%2FKolkata&weeks=4`);
    expect(result.weekly).toHaveLength(4);
  });
});

describe("fetchMoodOutlook", () => {
  it("sends the device timezone and interface locale", async () => {
    const { client, calls } = recordingClient(moodOutlook());

    const result = await fetchMoodOutlook({ locale: "bn" }, client);

    expect(calls[0]).toBe(`${INSIGHTS_ENDPOINTS.outlook}?tz=Asia%2FKolkata&locale=bn`);
    expect(result.forecast).toHaveLength(3);
  });
});
