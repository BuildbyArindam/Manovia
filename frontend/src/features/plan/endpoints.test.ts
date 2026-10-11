/**
 * The plan wire calls: paths, the timezone query, and the no-body done/skip.
 */
import { MemoryTokenStore, ApiClient, type FetchImpl } from "@/lib/api";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  PLAN_ENDPOINTS,
  fetchPlanSettings,
  fetchTodayPlan,
  markPlanItemDone,
  markPlanItemSkipped,
  savePlanSettings,
} from "./endpoints";
import { planSettings, planToday } from "@/test/planFakes";

function clientWith(fetchImpl: FetchImpl): ApiClient {
  const client = new ApiClient({ fetchImpl, tokens: new MemoryTokenStore() });
  client.applySession({ access_token: "access-1", refresh_token: "refresh-1", expires_in: 900 });
  return client;
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

async function record(input: string, init?: RequestInit): Promise<Response> {
  calls.push({
    url: input,
    method: init?.method ?? "GET",
    body: init?.body === undefined ? undefined : JSON.parse(init.body as string),
  });
  if (input.startsWith("/api/v1/plan/today")) {
    return jsonResponse(planToday());
  }
  if (input.startsWith("/api/v1/settings")) {
    return jsonResponse(planSettings());
  }
  return jsonResponse({});
}

describe("plan endpoints", () => {
  it("fetches today's plan with the device timezone", async () => {
    const client = clientWith(record);
    const plan = await fetchTodayPlan(client);
    expect(plan.count).toBe(1);
    expect(calls[0]?.url).toBe("/api/v1/plan/today?tz=Asia%2FKolkata");
  });

  it("marks an item done with a POST and no body", async () => {
    const client = clientWith(record);
    await markPlanItemDone("item-9", client);
    expect(calls[0]?.url).toBe(`/api/v1${PLAN_ENDPOINTS.done("item-9")}`);
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.body).toBeUndefined();
  });

  it("skips an item without asking for a reason", async () => {
    const client = clientWith(record);
    await markPlanItemSkipped("item-9", client);
    expect(calls[0]?.url).toBe(`/api/v1${PLAN_ENDPOINTS.skip("item-9")}`);
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.body).toBeUndefined();
  });

  it("reads the planner preferences", async () => {
    const client = clientWith(record);
    const settings = await fetchPlanSettings(client);
    expect(settings.quiet_start).toBe("22:00");
    expect(calls[0]?.url).toBe("/api/v1/settings");
  });

  it("saves the planner preferences with a PUT", async () => {
    const client = clientWith(record);
    await savePlanSettings(
      {
        preferred_exercise_tags: ["breathing"],
        quiet_hours_enabled: true,
        quiet_start: "21:00",
        quiet_end: "06:30",
      },
      client,
    );
    expect(calls[0]?.method).toBe("PUT");
    expect(calls[0]?.body).toEqual({
      preferred_exercise_tags: ["breathing"],
      quiet_hours_enabled: true,
      quiet_start: "21:00",
      quiet_end: "06:30",
    });
  });
});
