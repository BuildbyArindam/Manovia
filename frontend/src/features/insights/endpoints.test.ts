/**
 * The wire calls: what actually goes to the server.
 *
 * Two things matter here beyond the paths. First, every call carries the device's
 * IANA timezone, because a day boundary is a *local* thing — the same average
 * computed in UTC would disagree with the person's calendar. Second, the weekly
 * summary sends its locale, which is what makes the Hindi and Bengali templates
 * reachable at all.
 */

import { ApiClient, MemoryTokenStore, type FetchImpl } from "@/lib/api";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { insightsPatterns } from "@/test/insightsFakes";

import {
  INSIGHTS_ENDPOINTS,
  deviceTimezone,
  exportInsights,
  fetchInsightsPatterns,
  fetchWeeklySummary,
} from "./endpoints";
import { exportBundle, weeklySummary } from "@/test/insightsFakes";

function clientWith(fetchImpl: FetchImpl): ApiClient {
  const client = new ApiClient({ fetchImpl, tokens: new MemoryTokenStore() });
  client.applySession({ access_token: "access-1", refresh_token: "refresh-1", expires_in: 900 });
  return client;
}

let calls: Array<{ url: string; method: string }> = [];

function recordingFetch(payload: unknown): FetchImpl {
  return async (url, init) => {
    calls.push({ url, method: init?.method ?? "GET" });
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
}

beforeEach(() => {
  calls = [];
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
    timeZone: "Asia/Kolkata",
  } as Intl.ResolvedDateTimeFormatOptions);
});

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

describe("insights endpoints", () => {
  it("asks for the weekly summary with the timezone and the locale", async () => {
    const client = clientWith(recordingFetch(weeklySummary()));

    await fetchWeeklySummary({ locale: "hi" }, client);

    expect(calls).toHaveLength(1);
    const url = new URL(calls[0]?.url ?? "", "http://testserver");
    expect(url.pathname).toBe(`/api/v1${INSIGHTS_ENDPOINTS.weekly}`);
    expect(url.searchParams.get("tz")).toBe("Asia/Kolkata");
    expect(url.searchParams.get("locale")).toBe("hi");
  });

  it("defaults the weekly locale to English", async () => {
    const client = clientWith(recordingFetch(weeklySummary()));

    await fetchWeeklySummary({}, client);

    const url = new URL(calls[0]?.url ?? "", "http://testserver");
    expect(url.searchParams.get("locale")).toBe("en");
  });

  it("asks for patterns with the timezone", async () => {
    const client = clientWith(recordingFetch(insightsPatterns()));

    await fetchInsightsPatterns(client);

    const url = new URL(calls[0]?.url ?? "", "http://testserver");
    expect(url.pathname).toBe(`/api/v1${INSIGHTS_ENDPOINTS.patterns}`);
    expect(url.searchParams.get("tz")).toBe("Asia/Kolkata");
  });

  it("asks for the export with the timezone", async () => {
    const client = clientWith(recordingFetch(exportBundle()));

    await exportInsights(client);

    const url = new URL(calls[0]?.url ?? "", "http://testserver");
    expect(url.pathname).toBe(`/api/v1${INSIGHTS_ENDPOINTS.export}`);
    expect(url.searchParams.get("tz")).toBe("Asia/Kolkata");
  });

  it("surfaces the backend's error message rather than a bare status", async () => {
    const client = clientWith(async () =>
      new Response(
        JSON.stringify({
          error: { code: "invalid_timezone", message: "That timezone is not one this service knows.", request_id: "r-1" },
        }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(fetchInsightsPatterns(client)).rejects.toThrowError(
      /not one this service knows/,
    );
  });
});
