/**
 * Day 25 — offline, in a real browser, against the production build.
 *
 * What has to keep working with the network gone, in the order a person might
 * need it: "Need help now?" (the helplines), a breathing exercise, and a mood
 * check-in that is kept on the device and sent — exactly once, dated when it
 * was made — when the connection returns. Plus the journal draft that survives
 * an offline reload, and an honest message for a page that was never cached.
 *
 * "Served from the cache" is proven, not assumed: the API stamps every response
 * with a unique `X-Request-ID`, so the helpline answer read offline must carry
 * the id of the answer cached while online. A response from the network would
 * carry a new one.
 */

import { expect, test, type Page } from "@playwright/test";

import {
  API,
  accessToken,
  apiReachable,
  completeGuestOnboarding,
  waitForOfflineReady,
} from "./helpers";

const NOTE = "Checked in on the train with no signal.";

test.beforeEach(async ({ page }) => {
  test.skip(!(await apiReachable(page)), "the API is not reachable at /api/v1/ready");
});

async function helplineRequestId(page: Page): Promise<string | null> {
  return page.evaluate(async () => {
    const response = await fetch("/api/v1/crisis/resources");
    return response.ok ? response.headers.get("x-request-id") : `status-${response.status}`;
  });
}

test("help, breathing and a queued check-in work offline; the check-in syncs once", async ({
  page,
  context,
}) => {
  await completeGuestOnboarding(page);
  await waitForOfflineReady(page);

  // Online: the helplines answer comes from the network and refreshes the cache.
  const onlineId = await helplineRequestId(page);
  expect(onlineId).toMatch(/^[0-9a-f-]{8,}$/i);

  // ---- offline from here -------------------------------------------------
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByTestId("home-page")).toBeVisible();
  await expect(page.getByTestId("offline-banner")).toBeVisible();
  expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);

  // The offline helpline answer is the cached one (same request id), not a new one.
  expect(await helplineRequestId(page)).toBe(onlineId);

  // 1. "Need help now?" shows real helplines, and says they are the saved copy.
  await page.getByRole("button", { name: "Need help now?" }).first().click();
  const modal = page.getByTestId("crisis-modal");
  await expect(modal).toBeVisible();
  await expect(modal.getByTestId("crisis-offline-note")).toBeVisible();
  const telLinks = modal.locator('a[href^="tel:"]');
  await expect(telLinks.first()).toBeVisible();
  expect(await telLinks.count()).toBeGreaterThan(0);
  await modal.getByRole("button", { name: "Close" }).first().click();
  await expect(modal).toHaveCount(0);

  // 2. A breathing exercise: a full offline navigation, then the runner.
  await page.goto("/exercises");
  await expect(page.getByTestId("exercises-page")).toBeVisible();
  await page.getByRole("button", { name: "Try Box breathing" }).click();
  await page.getByRole("button", { name: "Start exercise" }).click();
  const timer = page.getByRole("timer");
  await expect(timer).toBeVisible();
  const first = await timer.textContent();
  await expect.poll(async () => timer.textContent(), { timeout: 6_000 }).not.toBe(first);
  await page.getByRole("button", { name: "Pause" }).click();
  await expect(page.getByText(/^Paused\./).first()).toBeVisible();
  await page.getByRole("button", { name: "Resume" }).click();
  await page.getByRole("button", { name: "Stop exercise" }).click();

  // 3. A mood check-in, queued on the device.
  await page.goto("/mood");
  await expect(page.getByTestId("mood-page")).toBeVisible();
  await expect(page.getByTestId("mood-offline-hint")).toBeVisible();
  await page.getByRole("radio", { name: "Very low" }).focus();
  await page.keyboard.press(" ");
  for (let i = 0; i < 3; i += 1) {
    await page.keyboard.press("ArrowRight");
  }
  await expect(page.getByRole("radio", { name: "Good", exact: true })).toBeChecked();
  await page.getByLabel(/anything you want to add/i).fill(NOTE);
  const madeAt = Date.now();
  await page.getByRole("button", { name: "Save check-in" }).click();

  await expect(page.getByTestId("mood-queued-confirmation")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Saved on this device" })).toBeFocused();
  await expect(page.getByTestId("pending-count")).toContainText("1 check-in is waiting");
  const queued = await page.evaluate(
    () => JSON.parse(window.localStorage.getItem("manovia.mood.outbox.v1") ?? "[]") as unknown[],
  );
  expect(queued).toHaveLength(1);

  // ---- back online: it sends itself, once --------------------------------
  const reconnectedAt = Date.now();
  await context.setOffline(false);
  await expect(page.getByTestId("pending-check-ins")).toHaveCount(0, { timeout: 30_000 });
  await expect(page.getByTestId("outbox-announcer")).toContainText("has been sent");
  expect(
    await page.evaluate(() => window.localStorage.getItem("manovia.mood.outbox.v1")),
  ).toBeNull();

  const token = await accessToken(page);
  const listed = await page.request.get(`${API}/mood?limit=20`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(listed.ok()).toBe(true);
  const body = (await listed.json()) as {
    entries: Array<{ note: string | null; created_at: string; valence: number }>;
  };
  const mine = body.entries.filter((entry) => entry.note === NOTE);
  expect(mine, "the queued check-in is stored exactly once").toHaveLength(1);
  expect(mine[0]?.valence).toBe(4);
  // Dated when it was made offline, not when it synced.
  const storedAt = Date.parse(mine[0]?.created_at ?? "");
  expect(storedAt).toBeGreaterThanOrEqual(madeAt - 2_000);
  expect(storedAt).toBeLessThanOrEqual(reconnectedAt);

  // The history shows it without a reload.
  await expect(page.getByTestId("mood-history-entry").first()).toContainText(NOTE);
});

test("a journal draft written offline survives a reload and saves on reconnect", async ({
  page,
  context,
}) => {
  await completeGuestOnboarding(page);
  await waitForOfflineReady(page);

  await context.setOffline(true);
  await page.goto("/journal");
  await expect(page.getByTestId("journal-view")).toBeVisible();
  await expect(page.getByTestId("journal-offline-note")).toBeVisible();
  const words = "Offline words that must not be lost.";
  await page.getByTestId("journal-body-input").fill(words);
  await expect(page.getByTestId("journal-save-status")).toContainText("kept on this device");
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem("manovia.journal.draft.v1")))
    .toContain(words);

  // Close and reopen while still offline: the draft is offered back, not applied silently.
  await page.reload();
  await expect(page.getByTestId("journal-draft-restore")).toBeVisible();
  await expect(page.getByTestId("journal-body-input")).toHaveValue("");
  await page.getByTestId("journal-draft-restore-button").click();
  await expect(page.getByTestId("journal-body-input")).toHaveValue(words);

  // Reconnect: the autosave sends it, and the local copy is removed.
  await context.setOffline(false);
  await expect(page.getByTestId("journal-save-status")).toContainText("Saved", { timeout: 30_000 });
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem("manovia.journal.draft.v1")))
    .toBeNull();

  const token = await accessToken(page);
  const listed = await page.request.get(`${API}/journal?limit=10`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(listed.ok()).toBe(true);
  const entries = ((await listed.json()) as { items: Array<{ body: string }> }).items;
  expect(entries.map((entry) => entry.body)).toContain(words);
});

test("a page that was never cached says so offline, and help stays one tap away", async ({
  page,
  context,
}) => {
  await completeGuestOnboarding(page);
  await waitForOfflineReady(page);

  await context.setOffline(true);
  // Insights (and its chart library) is deliberately left out of the precache.
  await page.goto("/insights");
  await expect(page.getByTestId("route-load-error")).toBeVisible();
  await expect(page.getByTestId("route-load-error")).toContainText(
    "needs a connection the first time",
  );
  await expect(page.getByRole("button", { name: "Need help now?" }).first()).toBeVisible();
});
