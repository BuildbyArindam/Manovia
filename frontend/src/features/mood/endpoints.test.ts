/**
 * The wire calls: what actually goes to the server.
 *
 * The timezone is the reason this file exists. Every mood call carries the
 * device's IANA zone because the streak is counted server-side in *that* zone —
 * so a request that forgot `tz` would quietly produce a streak that disagrees
 * with the person's calendar, which is exactly the kind of bug that shows up on
 * somebody's birthday at 00:30.
 */
import { MemoryTokenStore, ApiClient, type FetchImpl } from "@/lib/api";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  MOOD_ENDPOINTS,
  createMoodCheckIn,
  deleteMoodEntry,
  deviceTimezone,
  fetchMoodEntries,
  fetchMoodStreak,
} from "./endpoints";
import { moodEntry, moodStreak } from "@/test/moodFakes";

function clientWith(fetchImpl: FetchImpl): ApiClient {
  const client = new ApiClient({ fetchImpl, tokens: new MemoryTokenStore() });
  client.applySession({ access_token: "access-1", refresh_token: "refresh-1", expires_in: 900 });
  return client;
}

function first(): { url: string; method: string; body: unknown } {
  const call = calls[0];
  if (call === undefined) {
    throw new Error("no request was made");
  }
  return call;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let calls: Array<{ url: string; method: string; body: unknown }> = [];

beforeEach(() => {
  calls = [];
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
    timeZone: "Asia/Kolkata",
  } as Intl.ResolvedDateTimeFormatOptions);
});

function recordingFetch(payload: unknown, status = 200): FetchImpl {
  return async (url, init) => {
    calls.push({
      url,
      method: init?.method ?? "GET",
      body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
    });
    return jsonResponse(payload, status);
  };
}

describe("deviceTimezone", () => {
  it("reports the device zone", () => {
    expect(deviceTimezone()).toBe("Asia/Kolkata");
  });

  it("falls back to UTC when the browser will not say", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      timeZone: "",
    } as Intl.ResolvedDateTimeFormatOptions);

    expect(deviceTimezone()).toBe("UTC");
  });
});

describe("mood endpoints", () => {
  it("posts a check-in with the timezone attached", async () => {
    const client = clientWith(recordingFetch({ entry: moodEntry(), streak: moodStreak(), crisis: null }, 201));

    await createMoodCheckIn(
      { valence: 4, energy: 2, emotions: ["calm"], factors: ["sleep"], note: "ok" },
      client,
    );

    expect(calls).toHaveLength(1);
    expect(first().url).toBe(`/api/v1${MOOD_ENDPOINTS.checkIn}`);
    expect(first().method).toBe("POST");
    expect(first().body).toEqual({
      valence: 4,
      energy: 2,
      emotions: ["calm"],
      factors: ["sleep"],
      note: "ok",
      tz: "Asia/Kolkata",
    });
  });

  it("asks for a range in local days, with the timezone", async () => {
    const client = clientWith(
      recordingFetch({
        entries: [],
        count: 0,
        from: null,
        to: null,
        timezone: "Asia/Kolkata",
        timezone_fallback_used: false,
        limit: 100,
      }),
    );

    await fetchMoodEntries({ from: "2026-03-01", to: "2026-03-15", limit: 50 }, client);

    const url = new URL(first().url, "http://testserver");
    expect(url.pathname).toBe("/api/v1/mood");
    expect(url.searchParams.get("from")).toBe("2026-03-01");
    expect(url.searchParams.get("to")).toBe("2026-03-15");
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("tz")).toBe("Asia/Kolkata");
  });

  it("omits the range when none was asked for", async () => {
    const client = clientWith(
      recordingFetch({
        entries: [],
        count: 0,
        from: null,
        to: null,
        timezone: "Asia/Kolkata",
        timezone_fallback_used: false,
        limit: 100,
      }),
    );

    await fetchMoodEntries({}, client);

    const url = new URL(first().url, "http://testserver");
    expect(url.searchParams.has("from")).toBe(false);
    expect(url.searchParams.has("to")).toBe(false);
    expect(url.searchParams.get("tz")).toBe("Asia/Kolkata");
  });

  it("requests the streak with the timezone in the query string", async () => {
    const client = clientWith(recordingFetch(moodStreak()));

    await fetchMoodStreak(client);

    expect(first().url).toBe("/api/v1/mood/streak?tz=Asia%2FKolkata");
  });

  it("deletes one entry by id, still carrying the timezone", async () => {
    const client = clientWith(
      recordingFetch({ deleted: true, id: "entry-9", remaining_streak: moodStreak() }),
    );

    await deleteMoodEntry("entry-9", client);

    expect(first().url).toBe("/api/v1/mood/entry-9?tz=Asia%2FKolkata");
    expect(first().method).toBe("DELETE");
  });

  it("surfaces the backend's error code rather than a bare status", async () => {
    const client = clientWith(async () =>
      jsonResponse({ error: { code: "invalid_timezone", message: "nope", request_id: "r-1" } }, 422),
    );

    await expect(fetchMoodStreak(client)).rejects.toThrowError(/nope/);
  });
});
