/**
 * Shared helpers for the Day 25 production-build specs.
 *
 * Every spec registers its own guest through the real onboarding flow, so each
 * test starts from what a new reader sees and the backend's real consent gate
 * is exercised every time.
 */

import { expect, type Page } from "@playwright/test";

export const API = "/api/v1";

/** Every route the app serves, for the axe and zoom sweeps. */
export const APP_ROUTES = [
  { path: "/", name: "home", ready: "home-page" },
  { path: "/chat", name: "chat", ready: null },
  { path: "/mood", name: "mood", ready: "mood-page" },
  { path: "/journal", name: "journal", ready: "journal-view" },
  { path: "/cbt", name: "cbt", ready: null },
  { path: "/exercises", name: "exercises", ready: "exercises-page" },
  { path: "/biofeedback", name: "biofeedback", ready: "biofeedback-page" },
  { path: "/assessments", name: "assessments", ready: null },
  { path: "/insights", name: "insights", ready: null },
  { path: "/settings", name: "settings", ready: "install-section" },
  { path: "/accessibility", name: "accessibility", ready: "accessibility-page" },
  { path: "/no-such-page", name: "not-found", ready: null },
] as const;

export async function apiReachable(page: Page): Promise<boolean> {
  const response = await page.request.get(`${API}/ready`).catch(() => null);
  return response !== null && response.ok();
}

/** Walk the five onboarding steps as a guest (mouse is fine here). */
export async function completeGuestOnboarding(page: Page): Promise<void> {
  await page.goto("/");
  for (let step = 0; step < 3; step += 1) {
    await page.getByRole("button", { name: "Continue", exact: true }).click();
  }
  await page.locator("#consent-terms").check();
  await page.locator("#consent-privacy").check();
  await page.locator("#consent-ai_disclosure").check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Continue as a guest" }).click();
  await expect(page.getByTestId("home-page")).toBeVisible();
}

/** Navigate client-side and wait for a page to settle (heading + no loading text). */
export async function openRoute(page: Page, path: string, ready: string | null): Promise<void> {
  await page.goto(path);
  if (ready !== null) {
    await expect(page.getByTestId(ready)).toBeVisible();
  }
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  // Let pending queries settle so the scan sees the loaded page, not a spinner.
  await page.waitForLoadState("networkidle").catch(() => undefined);
}

/** Wait until the service worker controls the page and the offline caches are warm. */
export async function waitForOfflineReady(page: Page): Promise<void> {
  await page.waitForFunction(
    async () => {
      if (!("serviceWorker" in navigator)) {
        return false;
      }
      await navigator.serviceWorker.ready;
      if (navigator.serviceWorker.controller === null) {
        return false;
      }
      const helplines = await caches.open("manovia-helplines-v1");
      const exercises = await caches.open("manovia-exercises-v1");
      const [h, e] = await Promise.all([
        helplines.match("/api/v1/crisis/resources", { ignoreVary: true }),
        exercises.match("/api/v1/exercises", { ignoreVary: true }),
      ]);
      return h !== undefined && e !== undefined;
    },
    undefined,
    { timeout: 30_000, polling: 500 },
  );
}

/** The bearer token the app stored, for checking results through the API. */
export async function accessToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => {
    const raw = window.localStorage.getItem("manovia.tokens.v1");
    return raw === null ? null : (JSON.parse(raw) as { accessToken: string }).accessToken;
  });
  expect(token, "the app should hold a session after onboarding").not.toBeNull();
  return token as string;
}

/** Palette and contrast switches, applied the way the app applies them. */
export async function applyAppearance(
  page: Page,
  appearance: { theme: "light" | "dark"; contrast: "standard" | "more" },
): Promise<void> {
  await page.evaluate(({ theme, contrast }) => {
    window.localStorage.setItem("manovia.theme", theme);
    window.localStorage.setItem("manovia.contrast", contrast);
  }, appearance);
}
