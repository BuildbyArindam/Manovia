/**
 * Day 25 README screenshots, captured from the real production build.
 *
 * Skipped unless CAPTURE_SCREENSHOTS=1, so CI never rewrites committed images:
 *
 *   CAPTURE_SCREENSHOTS=1 npx playwright test -c playwright.prod.config.ts screenshots
 *
 * Only synthetic content appears: a fresh guest, a made-up check-in note, and
 * the helplines the shipped content file serves.
 */

import { expect, test, type Page } from "@playwright/test";

import { applyAppearance, completeGuestOnboarding, waitForOfflineReady } from "./helpers";

const OUT = "../docs/assets";

test.beforeEach(async ({ page }) => {
  test.skip(process.env.CAPTURE_SCREENSHOTS !== "1", "set CAPTURE_SCREENSHOTS=1 to capture");
  const ready = await page.request.get("/api/v1/ready").catch(() => null);
  test.skip(ready === null || !ready.ok(), "the API is not reachable");
});

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.evaluate(() => document.fonts.ready);
}

test("mobile: home, offline help, offline check-in", async ({ page, context }, info) => {
  test.skip(info.project.name !== "mobile-360x640", "mobile shots");
  await completeGuestOnboarding(page);
  await waitForOfflineReady(page);
  await settle(page);
  await page.screenshot({ path: `${OUT}/day25-home-mobile-360x640.png` });

  await context.setOffline(true);
  await page.reload();
  await expect(page.getByTestId("offline-banner")).toBeVisible();
  await page.getByRole("button", { name: "Need help now?" }).first().click();
  await expect(page.getByTestId("crisis-offline-note")).toBeVisible();
  await page.screenshot({ path: `${OUT}/day25-offline-help-mobile-360x640.png` });
  await page.keyboard.press("Escape");

  await page.goto("/mood");
  await page.getByRole("radio", { name: "Very low" }).focus();
  await page.keyboard.press(" ");
  await page.keyboard.press("ArrowRight");
  await page.getByLabel(/anything you want to add/i).fill("A made-up note, written offline.");
  await page.getByRole("button", { name: "Save check-in" }).click();
  await expect(page.getByTestId("mood-queued-confirmation")).toBeVisible();
  await page.getByTestId("pending-check-ins").scrollIntoViewIfNeeded();
  await page.getByTestId("mood-queued-confirmation").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/day25-offline-checkin-mobile-360x640.png` });
  await context.setOffline(false);
});

test("desktop: high contrast settings and the accessibility statement", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop-1280x800", "desktop shots");
  await completeGuestOnboarding(page);
  await applyAppearance(page, { theme: "light", contrast: "more" });
  await page.goto("/settings");
  await expect(page.locator("html")).toHaveAttribute("data-contrast", "more");
  await settle(page);
  await page.screenshot({ path: `${OUT}/day25-settings-high-contrast-desktop-1280x800.png` });

  await applyAppearance(page, { theme: "dark", contrast: "more" });
  await page.goto("/exercises");
  await expect(page.getByTestId("exercises-page")).toBeVisible();
  await settle(page);
  await page.screenshot({ path: `${OUT}/day25-exercises-dark-high-contrast-desktop-1280x800.png` });

  await applyAppearance(page, { theme: "light", contrast: "standard" });
  await page.goto("/accessibility");
  await expect(page.getByTestId("accessibility-page")).toBeVisible();
  await settle(page);
  await page.screenshot({ path: `${OUT}/day25-accessibility-statement-desktop-1280x800.png` });
});
