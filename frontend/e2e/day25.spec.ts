/**
 * Day 25 end-to-end verification suite:
 * 1. axe-core WCAG 2.2 AA audit across every route (plus High-Contrast Mode and
 *    the "Need help now?" dialog) — target: 0 serious/critical violations.
 * 2. Offline resilience: load the app, go offline, confirm "Need help now?"
 *    helplines and a breathing exercise still work; submit a mood check-in
 *    offline and show it syncs automatically after reconnecting.
 * 3. Keyboard-only walkthrough: onboarding -> chat -> mood -> exercise ->
 *    settings using Tab / Shift+Tab / Arrow keys / Space / Enter / Escape only.
 * 4. 200% and 400% zoom / text-resize reflow check across routes with zero
 *    horizontal overflow or clipped controls.
 */

import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const ASSETS_DIR = fileURLToPath(new URL("../../docs/assets", import.meta.url));

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

async function tabToElement(
  page: Page,
  predicate: () => Promise<boolean>,
  maxTabs = 60,
): Promise<void> {
  for (let i = 0; i < maxTabs; i += 1) {
    if (await predicate()) {
      return;
    }
    await page.keyboard.press("Tab");
  }
  throw new Error(`Keyboard walkthrough could not reach target within ${maxTabs} Tabs`);
}

test.describe("Day 25 — PWA, accessibility, keyboard walkthrough, and zoom reflow", () => {
  test("1. axe-core WCAG 2.2 AA across all routes has zero serious/critical violations", async ({
    page,
  }) => {
    await page.goto("/");
    const onboardingAxe = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
    expect(onboardingAxe.violations).toEqual([]);

    await completeGuestOnboarding(page);

    const routes = [
      "/",
      "/chat",
      "/mood",
      "/exercises",
      "/journal",
      "/cbt",
      "/assessments",
      "/insights",
      "/settings",
      "/accessibility",
    ];

    for (const route of routes) {
      await page.goto(route);
      await expect(page.locator("main h1").first()).toBeVisible();
      const scan = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
      const seriousOrCritical = scan.violations.filter(
        (v) => v.impact === "serious" || v.impact === "critical",
      );
      expect(
        seriousOrCritical,
        `Serious/critical axe violations on ${route}: ${JSON.stringify(seriousOrCritical)}`,
      ).toEqual([]);
      expect(scan.violations, `All axe violations on ${route}`).toEqual([]);
    }

    // Also audit High-Contrast Mode on /accessibility and the CrisisResourcesModal dialog
    await page.goto("/accessibility");
    await page.getByRole("radio", { name: /High contrast/ }).check();
    await expect(page.locator("html")).toHaveAttribute("data-contrast", "high");
    const highContrastScan = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
    expect(highContrastScan.violations).toEqual([]);
    if ((page.viewportSize()?.width ?? 0) >= 1000) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: `${ASSETS_DIR}/day25-accessibility-high-contrast-desktop-1280x800.png`,
        fullPage: false,
      });
    }

    await page.getByRole("button", { name: "Need help now?" }).first().click();
    await expect(page.getByTestId("crisis-modal")).toBeVisible();
    await expect(page.getByTestId("crisis-modal-card")).toBeVisible();
    const modalScan = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
    expect(modalScan.violations).toEqual([]);
    await page.keyboard.press("Escape");
  });

  test("2. Offline resilience: helplines and breathing work offline; queued mood check-in syncs on reconnect", async ({
    page,
    context,
  }) => {
    await completeGuestOnboarding(page);

    // Wait briefly so the warmOfflineResources prefetch + lazy route preload complete
    await page.waitForFunction(() => {
      return (
        window.localStorage.getItem("manovia.offline.crisis.v1") !== null &&
        window.localStorage.getItem("manovia.offline.exercises.v1") !== null
      );
    });

    // Go offline
    await context.setOffline(true);
    await page.evaluate(() => {
      window.dispatchEvent(new Event("offline"));
    });
    await expect(page.getByTestId("offline-banner")).toBeVisible();

    // 1) Confirm "Need help now?" modal shows verified helplines while offline
    await page.getByRole("button", { name: "Need help now?" }).first().click();
    const modal = page.getByTestId("crisis-modal");
    await expect(modal).toBeVisible();
    const card = page.getByTestId("crisis-modal-card");
    await expect(card).toBeVisible();
    const telLinks = card.locator('a[href^="tel:"]');
    expect(await telLinks.count()).toBeGreaterThan(0);
    await page.keyboard.press("Escape");
    await expect(modal).toBeHidden();

    // 2) Navigate to /exercises via SPA nav while offline and run Box breathing
    await page.getByRole("link", { name: /^Exercises/ }).click();
    await expect(page.getByTestId("exercises-page")).toBeVisible();
    await page.getByRole("button", { name: "Try Box breathing" }).click();
    await expect(page.getByTestId("exercise-runner")).toBeVisible();
    await page.getByRole("button", { name: "Start exercise" }).click();
    await expect(page.getByTestId("exercise-instruction")).toBeVisible();
    await page.getByRole("button", { name: "Stop exercise" }).click();

    // 3) Submit a mood check-in while offline
    await page.getByRole("link", { name: /^Mood/ }).click();
    await expect(page.getByTestId("mood-page")).toBeVisible();
    await page.getByTestId("mood-face-4").click();
    await page.getByRole("checkbox", { name: "Sleep", exact: true }).focus();
    await page.keyboard.press(" ");
    await page.getByLabel(/Anything you want to add/i).fill("Offline evening check-in");
    await page.getByRole("button", { name: "Save check-in" }).click();

    await expect(page.getByTestId("mood-queued-offline")).toBeVisible();
    await expect(page.getByTestId("offline-queue-count")).toContainText("1 queued");
    await expect(page.getByTestId("mood-queued-badge")).toContainText("Waiting to sync");
    if ((page.viewportSize()?.width ?? 0) <= 500) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: `${ASSETS_DIR}/day25-offline-mood-queue-mobile-360x640.png`,
        fullPage: false,
      });
    }

    // 4) Reconnect and verify the queued check-in syncs automatically
    await context.setOffline(false);
    await page.evaluate(() => {
      window.dispatchEvent(new Event("online"));
    });

    await expect(page.getByTestId("mood-offline-synced")).toContainText("Synced 1");
    await expect(page.getByTestId("mood-queued-badge")).toHaveCount(0);
    await expect(page.getByTestId("mood-history")).toContainText("Offline evening check-in");
  });

  test("3. Keyboard-only walkthrough: onboarding -> chat -> mood -> exercise -> settings", async ({
    page,
  }) => {
    await page.goto("/");

    // Step 1-3 of onboarding via keyboard only
    for (let step = 0; step < 3; step += 1) {
      await tabToElement(page, () =>
        page.evaluate(
          () => document.activeElement?.textContent?.trim() === "Continue",
        ),
      );
      await page.keyboard.press("Enter");
    }

    // Step 4: tick the three required agreements with Space, then press Continue
    for (const id of ["consent-terms", "consent-privacy", "consent-ai_disclosure"]) {
      await tabToElement(page, () =>
        page.evaluate((targetId) => document.activeElement?.id === targetId, id),
      );
      await page.keyboard.press("Space");
    }
    await tabToElement(page, () =>
      page.evaluate(() => document.activeElement?.textContent?.trim() === "Continue"),
    );
    await page.keyboard.press("Enter");

    // Step 5: Continue as a guest with Enter
    await tabToElement(page, () =>
      page.evaluate(
        () => document.activeElement?.textContent?.trim() === "Continue as a guest",
      ),
    );
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("home-page")).toBeVisible();

    // Navigate to Chat via keyboard on primary navigation
    await tabToElement(page, () =>
      page.evaluate(
        () => document.activeElement?.getAttribute("href") === "/chat",
      ),
    );
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/chat$/);

    // Focus the chat composer via Tab, type a message, and send with Enter
    await tabToElement(page, () =>
      page.evaluate(() => document.activeElement?.tagName === "TEXTAREA"),
    );
    await page.keyboard.type("Checking in with keyboard only");
    await page.keyboard.press("Enter");
    await expect(page.getByText("Checking in with keyboard only")).toBeVisible();

    // Navigate to Mood via keyboard
    await page.locator("body").focus();
    await tabToElement(page, () =>
      page.evaluate(() => document.activeElement?.getAttribute("href") === "/mood"),
    );
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/mood$/);

    // Select a valence radio with keyboard and submit the mood check-in
    await tabToElement(page, () =>
      page.evaluate(
        () =>
          document.activeElement instanceof HTMLInputElement &&
          document.activeElement.type === "radio",
      ),
    );
    await page.keyboard.press("Space");
    await tabToElement(page, () =>
      page.evaluate(
        () => document.activeElement?.textContent?.trim() === "Save check-in",
      ),
    );
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("mood-confirmation")).toBeVisible();

    // Navigate to Exercises via keyboard, launch Box breathing, and return
    await page.locator("body").focus();
    await tabToElement(page, () =>
      page.evaluate(
        () => document.activeElement?.getAttribute("href") === "/exercises",
      ),
    );
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/exercises$/);

    await tabToElement(page, () =>
      page.evaluate(
        () => document.activeElement?.textContent?.trim() === "Try Box breathing",
      ),
    );
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Box breathing" })).toBeFocused();

    await tabToElement(page, () =>
      page.evaluate(
        () => document.activeElement?.textContent?.trim() === "Back to exercises",
      ),
    );
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("button", { name: "Try Box breathing" }),
    ).toBeFocused();

    // Navigate to Settings via keyboard and toggle High Contrast
    await page.locator("body").focus();
    await tabToElement(page, () =>
      page.evaluate(
        () => document.activeElement?.getAttribute("href") === "/settings",
      ),
    );
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/settings$/);

    await tabToElement(page, () =>
      page.evaluate(
        () =>
          document.activeElement instanceof HTMLInputElement &&
          document.activeElement.name === "contrast",
      ),
    );
    await page.keyboard.press("ArrowLeft");
    await expect(page.locator("html")).toHaveAttribute("data-contrast", "high");
  });

  test("4. Zoom to 200% and 400% has zero horizontal overflow or layout loss", async ({
    page,
  }) => {
    await completeGuestOnboarding(page);

    const zoomCases = [
      { label: "200% zoom (640x400 + 200% font scale)", width: 640, height: 400, fontPx: 32 },
      { label: "400% zoom (320x256 WCAG 1.4.10 reflow)", width: 320, height: 256, fontPx: 16 },
    ];
    const routes = ["/", "/chat", "/mood", "/exercises", "/settings", "/accessibility"];

    for (const zoom of zoomCases) {
      await page.setViewportSize({ width: zoom.width, height: zoom.height });
      for (const route of routes) {
        await page.goto(route);
        await page.evaluate((px) => {
          document.documentElement.style.fontSize = `${px}px`;
        }, zoom.fontPx);
        await expect(page.locator("main h1").first()).toBeVisible();
        await expect(page.getByRole("button", { name: "Need help now?" }).first()).toBeVisible();

        const metrics = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
          innerWidth: window.innerWidth,
        }));

        expect(
          metrics.scrollWidth,
          `Horizontal overflow on ${route} at ${zoom.label}: scrollWidth=${metrics.scrollWidth} > innerWidth=${metrics.innerWidth}`,
        ).toBeLessThanOrEqual(metrics.innerWidth + 1);
      }
    }
  });
});
