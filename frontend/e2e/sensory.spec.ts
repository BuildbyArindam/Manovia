/**
 * Day 46 — sensory profile, overwhelmed mode, reduced motion and the WebXR
 * fallback, against the real app in a real browser.
 *
 * These are the checks the sandbox could not run (no downloadable Playwright
 * browser here); they are written to be run with `npm run test:e2e`. The same
 * behaviours are covered in Node under Vitest, which is what ran in CI.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

function seedOnboarding(): void {
  window.localStorage.setItem(
    "manovia.onboarding.v1",
    JSON.stringify({
      version: 1,
      completedAt: new Date().toISOString(),
      consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: false },
      authMode: "guest",
      user: { id: "guest-e2e", email: null, isAnonymous: true },
    }),
  );
}

async function openSettings(page: Page): Promise<void> {
  await page.addInitScript(seedOnboarding);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { level: 2, name: "Sensory profile" })).toBeVisible();
}

async function expectAxeClean(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations,
    `${label}: ${results.violations.length} violations\n${JSON.stringify(results.violations, null, 2)}`,
  ).toEqual([]);
}

test.describe("sensory profile", () => {
  test("each choice applies immediately, persists, and is reversible", async ({ page }) => {
    await openSettings(page);
    const html = page.locator("html");

    await page.getByRole("radio", { name: /^None/ }).check();
    await expect(html).toHaveAttribute("data-sensory-motion", "none");
    await page.getByRole("radio", { name: /^Off/ }).check();
    await expect(html).toHaveAttribute("data-sensory-sound", "off");
    await page.getByRole("radio", { name: /^Simple/ }).check();
    await expect(html).toHaveAttribute("data-sensory-density", "simple");
    await page.getByRole("radio", { name: /^High contrast/ }).check();
    await expect(html).toHaveAttribute("data-sensory-colours", "high-contrast");
    await page.getByRole("radio", { name: /^Larger/ }).check();
    await expect(html).toHaveAttribute("data-sensory-font", "larger");

    const background = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--manovia-bg").trim(),
    );
    expect(background).toBe("#fffdf8"); // the high-contrast light palette
    const fontScale = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--manovia-font-scale").trim(),
    );
    expect(Number(fontScale)).toBe(1.25);

    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-sensory-motion", "none");
    await expect(page.locator("html")).toHaveAttribute("data-sensory-font", "larger");

    await page.getByRole("button", { name: "Reset to defaults" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-sensory-motion", "normal");
    await expect(page.locator("html")).toHaveAttribute("data-sensory-colours", "calm");
    await expectAxeClean(page, "sensory settings");
  });

  test("prefers-reduced-motion wins over the stored choice and the XR entry is feature-detected", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openSettings(page);
    // The stored choice stays "normal", but the effective motion is none.
    await expect(page.locator("html")).toHaveAttribute("data-sensory-motion", "none");
    await expect(page.locator("html")).toHaveAttribute("data-sensory-motion-choice", "normal");

    await page.goto("/calm");
    await expect(page.getByTestId("calm-static-note")).toBeVisible();
    // Headless Chromium has no WebXR: the 2D fallback is what must load.
    await expect(page.getByTestId("calm-xr-fallback")).toBeVisible();
    await expect(page.getByTestId("calm-enter-vr")).toHaveCount(0);
    expect(await page.evaluate(() => typeof (navigator as Navigator & { xr?: unknown }).xr)).toBe(
      "undefined",
    );
  });

  test("'I'm overwhelmed' hides charts and badges and leaves four ways out", async ({ page }) => {
    await openSettings(page);
    await page.getByTestId("overwhelmed-toggle").click();

    await expect(page.locator("html")).toHaveAttribute("data-overwhelmed", "true");
    await expect(page.getByTestId("overwhelmed-bar")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" })).toHaveCount(0);
    for (const [label, href] of [
      [/Breathe/, "/calm"],
      [/Ground/, "/exercises"],
      [/Talk/, "/chat"],
    ] as const) {
      await expect(page.getByRole("link", { name: label }).first()).toHaveAttribute("href", href);
    }
    await expect(page.getByTestId("overwhelmed-help")).toBeVisible();

    // CSS is the second line of defence for markup outside React.
    const chartDisplay = await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.setAttribute("data-sensory", "chart");
      document.body.append(probe);
      const display = getComputedStyle(probe).display;
      probe.remove();
      return display;
    });
    expect(chartDisplay).toBe("none");

    await expectAxeClean(page, "overwhelmed settings");

    await page.getByTestId("overwhelmed-exit").click();
    await expect(page.locator("html")).not.toHaveAttribute("data-overwhelmed");
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  });
});
