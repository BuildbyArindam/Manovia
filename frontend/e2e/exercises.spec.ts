/** Day 18: real YAML + API, browser clocks, offline retry, and axe (no API mocks). */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import type { ExerciseDefinition } from "../src/features/exercises/types";

function report(message: string, details?: unknown): void {
  process.stdout.write(`${message}${details === undefined ? "" : ` ${JSON.stringify(details)}`}\n`);
}

async function onboard(page: Page): Promise<void> {
  await page.goto("/");
  for (let step = 0; step < 3; step += 1) {
    await page.getByRole("button", { name: "Continue", exact: true }).click();
  }
  await page.locator("#consent-terms").check();
  await page.locator("#consent-privacy").check();
  await page.locator("#consent-ai_disclosure").check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Continue as a guest" }).click();
  await page.getByRole("link", { name: /^Exercises\b/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Exercises" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Try Box breathing" })).toBeVisible();
}

async function box(page: Page): Promise<ExerciseDefinition> {
  const response = await page.request.get("/api/v1/exercises/box-breathing");
  expect(response.status()).toBe(200);
  await page.getByRole("button", { name: "Try Box breathing" }).click();
  await expect(page.getByRole("heading", { name: "Box breathing", exact: true })).toBeFocused();
  await expect(page.getByText(/If breath-holding makes you dizzy/)).toBeVisible();
  return response.json() as Promise<ExerciseDefinition>;
}

async function freezeClock(page: Page): Promise<void> {
  await page.clock.install({ time: new Date("2026-10-11T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-10-11T12:00:01Z"));
}

/** Run each real YAML step separately, so no intermediate instruction is skipped. */
async function finishSteps(page: Page, definition: ExerciseDefinition, from = 0): Promise<void> {
  for (const step of definition.steps.slice(from)) {
    await expect(page.getByTestId("exercise-instruction")).toHaveText(step.instruction);
    await page.clock.runFor(step.duration_seconds * 1000);
  }
  await expect(page.getByTestId("exercise-finished")).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  // A missing API fails this Day 18 verification; never silently skip it.
  const ready = await page.request.get("/api/v1/ready");
  expect(ready.ok(), "bring up the migrated backend before running Day 18 e2e").toBe(true);
});

test("reduced-motion breathing uses static countdown and visible instructions, then saves ratings/history", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await onboard(page);
  await page.getByLabel("How are you feeling?").selectOption("anxiety");
  await page.getByLabel("How strong does it feel?").selectOption("high");
  const card = page.getByTestId("exercise-card-box-breathing");
  await card.getByText("Why this?", { exact: true }).click();
  await expect(card).toContainText("You selected high anxiety");
  const definition = await box(page);
  const beforeRating = page.getByRole("radio", { name: "Low", exact: true });
  await beforeRating.focus();
  await page.keyboard.press("Space");
  await expect(beforeRating).toBeChecked();
  await freezeClock(page);
  await page.getByRole("button", { name: "Start exercise" }).click();
  await expect(page.getByTestId("static-countdown")).toBeVisible();
  await expect(page.getByTestId("breathing-guide")).toHaveAttribute(
    "data-motion",
    "static-countdown",
  );
  expect(await page.getByTestId("breathing-guide").locator("svg").count()).toBe(0);
  await expect(page.getByTestId("exercise-instruction")).toBeVisible();
  await expect(page.getByTestId("exercise-instruction")).toHaveText(
    definition.steps[0]!.instruction,
  );
  await page.clock.runFor(definition.steps[0]!.duration_seconds * 1000);
  await expect(page.getByTestId("exercise-instruction")).toHaveText(
    definition.steps[1]!.instruction,
  );
  await expect(page.getByTestId("step-countdown")).toHaveText("4");
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  const pausedCount = await page.getByTestId("step-countdown").innerText();
  await page.clock.runFor(10000);
  await expect(page.getByTestId("step-countdown")).toHaveText(pausedCount);
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await finishSteps(page, definition, 1);
  const afterRating = page.getByRole("radio", { name: "Good", exact: true });
  await afterRating.focus();
  await page.keyboard.press("Space");
  await expect(afterRating).toBeChecked();
  const historyResponse = page.waitForResponse((result) =>
    result.url().includes("/exercises/history?"),
  );
  const response = page.waitForResponse(
    (result) =>
      result.url().endsWith("/box-breathing/complete") && result.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save completion" }).click();
  const saved = await response;
  expect(saved.status()).toBe(201);
  expect((await saved.json()).change).toBe(2);
  await historyResponse;
  await page.clock.runFor(1); // flush the query observer notification
  await expect(page.getByTestId("completion-saved")).toContainText("not proof");
  await expect(page.getByTestId("exercise-history-entry").first()).toContainText("Before: 2 of 5");
  await expect(page.getByTestId("exercise-history-entry").first()).toContainText("2 points higher");
  report(
    "Reduced motion: static countdown, no animated SVG, all 26 visible step instructions, pause/resume and API/history passed.",
  );
});

test("loaded runner works offline and explicitly retries unsaved completion without duplication", async ({
  page,
  context,
}) => {
  await onboard(page);
  const definition = await box(page);
  const payloads: unknown[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/box-breathing/complete") && request.method() === "POST") {
      payloads.push(request.postDataJSON());
    }
  });
  await freezeClock(page);
  await context.setOffline(true);
  await page.getByRole("button", { name: "Start exercise" }).click();
  await finishSteps(page, definition);
  await page.getByRole("button", { name: "Save completion" }).click();
  await expect(page.getByTestId("completion-unsaved")).toContainText("Not saved");
  await expect(page.getByTestId("completion-saved")).toHaveCount(0);
  await context.setOffline(false);
  const historyResponse = page.waitForResponse((result) =>
    result.url().includes("/exercises/history?"),
  );
  await page.getByRole("button", { name: "Retry saving" }).click();
  await historyResponse;
  await page.clock.runFor(1);
  await expect(page.getByTestId("completion-saved")).toBeVisible();
  expect(payloads).toHaveLength(2);
  expect(payloads[0]).toEqual(payloads[1]);
  await expect(page.getByTestId("exercise-history-entry")).toHaveCount(1);
  await expect(page.getByTestId("exercise-history-entry")).toContainText("Both optional ratings");
  report(
    "Offline: all loaded steps ran; failed save was marked unsaved; same-payload retry produced one history record.",
  );
});

test("axe runner has no violations in light/dark themes and animated breathing remains text-labelled", async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await onboard(page);
  await box(page);
  await page.getByText("Notes and evidence", { exact: true }).click();
  for (const theme of ["light", "dark"]) {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
    }, theme);
    const result = await new AxeBuilder({ page })
      .include('[data-testid="exercise-runner"]')
      .analyze();
    report(
      `axe ready ${testInfo.project.name} ${theme}: ${result.violations.length} violations`,
      result.violations.map((item) => item.id),
    );
    await testInfo.attach(`axe-ready-${theme}`, {
      body: JSON.stringify(result),
      contentType: "application/json",
    });
    expect(result.violations).toEqual([]);
  }
  await page.getByRole("button", { name: "Start exercise" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("exercise-instruction")).toContainText("Breathe in gently", {
    timeout: 22000,
  });
  const guide = page.getByTestId("animated-breathing-guide");
  await expect(guide).toBeVisible();
  expect(await guide.locator("svg").evaluate((svg) => getComputedStyle(svg).animationName)).toBe(
    "exercise-inhale",
  );
  await page.getByRole("button", { name: "Pause", exact: true }).focus();
  await page.keyboard.press("Enter");
  expect(
    await guide.locator("svg").evaluate((svg) => getComputedStyle(svg).animationPlayState),
  ).toBe("paused");
  for (const theme of ["light", "dark"]) {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
    }, theme);
    const result = await new AxeBuilder({ page })
      .include('[data-testid="exercise-runner"]')
      .analyze();
    report(
      `axe paused ${testInfo.project.name} ${theme}: ${result.violations.length} violations`,
      result.violations.map((item) => item.id),
    );
    await testInfo.attach(`axe-paused-${theme}`, {
      body: JSON.stringify(result),
      contentType: "application/json",
    });
    expect(result.violations).toEqual([]);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
