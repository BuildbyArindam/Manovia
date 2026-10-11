/**
 * Day 21 end-to-end: the home screen — greeting, plan card, quick check-in.
 *
 * Run against the real backend (no model is in this path):
 *
 *   # terminal 1 — the API
 *   cd backend && SECRET_KEY=dev-only FIELD_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") make dev
 *   # terminal 2
 *   cd frontend && npm run test:e2e -- home.spec.ts
 *
 * The spec registers a fresh guest, so the planner must show the new-person
 * plan (one gentle check-in invitation with a "why") and accept a done/skip
 * round trip over the real API.
 */

import { expect, test, type Page } from "@playwright/test";

/** Walk the five onboarding steps as a guest and land on the home screen. */
async function completeGuestOnboarding(page: Page): Promise<void> {
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

test.beforeEach(async ({ page }) => {
  const response = await page.request.get("/api/v1/ready").catch(() => null);
  test.skip(response === null || !response.ok(), "the API is not reachable at /api/v1/ready");
});

test("the home screen greets, plans, and checks in", async ({ page }) => {
  await completeGuestOnboarding(page);

  // The greeting exists, is time-of-day shaped, and assumes no name or gender.
  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading).toBeVisible();
  await expect(heading).toContainText(/Good (morning|afternoon|evening)/);

  // The persistent help control is on the page itself, not only the header.
  await expect(
    page.getByTestId("home-page").getByRole("button", { name: "Need help now?" }).first(),
  ).toBeVisible();

  // A new person's plan: one suggestion, with a "why".
  await expect(page.getByTestId("plan-card")).toBeVisible();
  await expect(page.getByTestId("plan-item-check_in")).toBeVisible();
  await expect(page.getByTestId("plan-item-check_in")).toContainText("Why this?");

  // Quick check-in, keyboard only: tab into the faces, pick one, then save.
  const checkIn = page.getByTestId("quick-check-in");
  await checkIn.getByRole("radio").first().focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await checkIn.getByRole("button", { name: "Save check-in" }).click();
  await expect(checkIn.getByRole("status")).toContainText("Saved");

  // Settle the plan item: done needs no extra input.
  await page.getByTestId("plan-item-check_in").getByRole("button", { name: "Done" }).click();
  await expect(page.getByTestId("plan-item-check_in")).toHaveAttribute("data-status", "done");

  // The plan does not grow past three items, whatever we reload.
  await page.reload();
  await expect(page.getByTestId("plan-card")).toBeVisible();
  expect(await page.getByTestId(/^plan-item-/).count()).toBeLessThanOrEqual(3);

  // The chat door is one click away.
  await page.getByTestId("home-chat-link").click();
  await expect(page).toHaveURL(/\/chat$/);
});
