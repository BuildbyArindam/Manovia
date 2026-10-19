/**
 * Day 46 — the calm space in a real browser: fallback, comfort controls, a
 * frame-time sample and axe.
 *
 * `test:e2e` expects the backend on :8000 (see playwright.config.ts), but this
 * page is entirely client-side; the onboarding flag is seeded so no API call
 * decides whether it renders.
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

async function openCalm(page: Page): Promise<void> {
  await page.addInitScript(seedOnboarding);
  await page.goto("/calm");
  await expect(page.getByRole("heading", { level: 1, name: "Calm space" })).toBeVisible();
}

function report(message: string): void {
  process.stdout.write(`${message}\n`);
}

test.describe("calm space", () => {
  test("falls back to 2D with the full safety note, and runs the breathing cue", async ({
    page,
  }) => {
    await openCalm(page);

    await expect(page.getByTestId("calm-xr-fallback")).toContainText("2D calm space");
    await expect(page.getByTestId("calm-enter-vr")).toHaveCount(0);

    const safety = page.getByTestId("calm-vr-section");
    await expect(safety).toContainText("not recommended for everyone");
    await expect(safety).toContainText("pregnant");
    await expect(safety).toContainText("epilepsy");
    await expect(safety).toContainText("age limit");
    await expect(safety).toContainText("sitting still");
    await expect(safety).toContainText("One controller press leaves VR");

    await page.getByTestId("calm-start").click();
    await expect(page.getByTestId("calm-pause")).toBeVisible();
    // The phase is text, not only a picture, and it changes with the breath.
    await expect(page.getByTestId("calm-phase")).toHaveText(/Breathe (in|out)/);
    await page.getByTestId("calm-pause").click();
    await expect(page.getByTestId("calm-resume")).toBeVisible();

    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });

  test("canvas is decorative and the scene has a text description", async ({ page }) => {
    await openCalm(page);
    await expect(page.getByTestId("calm-canvas")).toHaveAttribute("aria-hidden", "true");
    await expect(page.getByText(/still evening lake/i)).toBeAttached();
  });

  test("records real frame intervals for the 2D fallback", async ({ page }) => {
    await openCalm(page);
    await page.getByTestId("calm-start").click();
    const samples = await page.evaluate(async () => {
      const intervals: number[] = [];
      await new Promise<void>((resolve) => {
        let previous = performance.now();
        const tick = (now: number): void => {
          if (intervals.length > 5) intervals.push(now - previous);
          previous = now;
          if (intervals.length >= 180) resolve();
          else requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
      return intervals;
    });
    const sorted = [...samples].sort((a, b) => a - b);
    const at = (fraction: number): number =>
      sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))] ?? 0;
    const p50 = at(0.5);
    const p5 = at(0.05);
    report(
      `calm-space 2D rAF intervals: p50 ${p50.toFixed(2)} ms (${(1000 / p50).toFixed(1)} FPS), ` +
        `p5 ${p5.toFixed(2)} ms (${(1000 / p5).toFixed(1)} FPS), n=${samples.length}`,
    );
    expect(p50).toBeGreaterThan(0);
  });
});
