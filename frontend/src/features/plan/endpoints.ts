/**
 * The daily-plan wire calls.
 *
 * Every plan read carries the device's IANA timezone for the same reason the
 * mood endpoints do: the planner decides what "today" is server-side in that
 * zone, and a request that forgot `tz` would produce a plan that disagrees
 * with the person's calendar around midnight.
 */

import { api as defaultClient, type ApiClient } from "@/lib/api";
import { currentAppLanguage } from "@/lib/i18n/i18n";
import { deviceTimezone } from "@/lib/timezone";

import type { PlanItem, PlanSettings, PlanSettingsPayload, PlanTodayResponse } from "./types";

export { deviceTimezone };

export const planSettingsKeys = {
  current: ["plan", "settings"] as const,
};

export const PLAN_ENDPOINTS = {
  today: "/plan/today",
  done: (id: string): string => `/plan/${id}/done`,
  skip: (id: string): string => `/plan/${id}/skip`,
  settings: "/settings",
} as const;

/**
 * Today's plan, counted in this device's timezone and worded in the
 * interface language (Day 23). A plan is generated once per local day, so the
 * language of the first request of the day is the one the plan keeps.
 */
export async function fetchTodayPlan(
  client: ApiClient = defaultClient,
  locale: string = currentAppLanguage(),
): Promise<PlanTodayResponse> {
  const params = new URLSearchParams({
    tz: deviceTimezone(),
    locale,
  });
  return client.get<PlanTodayResponse>(`${PLAN_ENDPOINTS.today}?${params.toString()}`);
}

/** Mark one suggestion done. No body; the server owns the transition. */
export async function markPlanItemDone(
  id: string,
  client: ApiClient = defaultClient,
): Promise<PlanItem> {
  return client.post<PlanItem>(PLAN_ENDPOINTS.done(id));
}

/** Skip one suggestion. No reason is sent, and none is asked for. */
export async function markPlanItemSkipped(
  id: string,
  client: ApiClient = defaultClient,
): Promise<PlanItem> {
  return client.post<PlanItem>(PLAN_ENDPOINTS.skip(id));
}

/** The planner preferences (defaults when never saved). */
export async function fetchPlanSettings(client: ApiClient = defaultClient): Promise<PlanSettings> {
  return client.get<PlanSettings>(PLAN_ENDPOINTS.settings);
}

/** Replace the planner preferences. */
export async function savePlanSettings(
  payload: PlanSettingsPayload,
  client: ApiClient = defaultClient,
): Promise<PlanSettings> {
  return client.request<PlanSettings>(PLAN_ENDPOINTS.settings, {
    method: "PUT",
    body: payload,
  });
}
