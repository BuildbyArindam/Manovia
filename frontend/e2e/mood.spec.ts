/**
 * Day 15 end-to-end: a whole mood check-in, keyboard only.
 *
 * Run against the real backend (the Fake LLM is not even involved — no model is
 * in this path):
 *
 *   # terminal 1 — the API
 *   cd backend && SECRET_KEY=dev-only FIELD_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") make dev
 *   # terminal 2
 *   cd frontend && npm run test:e2e -- mood.spec.ts
 *
 * Why this cannot be a unit test:
 *
 * 1. **Arrow-key stepping on a range input does not exist in jsdom.** The energy
 *    slider is a real form control and its keyboard behaviour is the browser's,
 *    so it is only testable here.
 * 2. **Focus order is a browser fact.** That `Tab` from the last chip lands on the
 *    note, and from the note on Save, depends on real tabbability — including the
 *    visually-hidden radios, which a jsdom test can only assume.
 * 3. **The check-in crosses a real HTTP connection** through the Vite dev proxy,
 *    with the real safety engine behind it, and comes back as JSON the page
 *    renders — the contract, not a mock.
 *
 * No mouse is used anywhere in the check-in test: only `keyboard.press`.
 */

import { expect, test, type Page } from "@playwright/test";

/** Read by the API's rules engine as HIGH: the crisis metadata path. */
const HIGH_RISK_NOTE = "I want to kill myself";
const ORDINARY_NOTE = "Long day, but I ate something proper.";

/** Walk the five onboarding steps as a guest and land on /mood. */
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

  // Day 21: onboarding lands on the home screen, which has its own mood
  // check-in link. Wait for the guest session to settle there, then go to the
  // full mood page this spec exercises.
  await expect(page.getByTestId("home-page")).toBeVisible();
  await page.goto("/mood");
  await expect(page.getByRole("heading", { level: 1, name: "Mood" })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  const response = await page.request.get("/api/v1/ready").catch(() => null);
  test.skip(response === null || !response.ok(), "the API is not reachable at /api/v1/ready");
});

test("a whole check-in is completable with the keyboard alone", async ({ page }) => {
  await completeGuestOnboarding(page);
  await expect(page.getByTestId("mood-page")).toBeVisible();

  // 1. Tab to the first face, choose it, then move right to "Good".
  await page.keyboard.press("Tab");
  // Tabbing through the page reaches the radios; drive them explicitly instead of
  // counting tabs, so a nav change cannot make this fail for the wrong reason.
  await page.getByRole("radio", { name: "Very low" }).focus();
  await page.keyboard.press(" ");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  // exact: "Good" is also a substring of the "Very good" face's label.
  await expect(page.getByRole("radio", { name: "Good", exact: true })).toBeChecked();
  await expect(page.getByTestId("mood-face-4")).toHaveAttribute("data-selected", "true");

  // 2. Energy: focus the slider and step it down with the arrow keys. This is the
  //    part jsdom cannot do.
  const slider = page.getByRole("slider", { name: /energy/i });
  await slider.focus();
  await expect(slider).toHaveAttribute("aria-valuetext", "Some");
  await page.keyboard.press("ArrowLeft");
  await expect(slider).toHaveAttribute("aria-valuetext", "Running low");
  await expect(page.getByTestId("mood-energy-readout")).toContainText("2 — Running low");

  // 3. Chips: Space toggles, and the emotions cap (three) is explained rather
  //    than silent. Factors are a separate group with their own cap, so the
  //    "Sleep" factor stays available once three emotions are chosen.
  await page.getByRole("checkbox", { name: "Calm" }).focus();
  await page.keyboard.press(" ");
  await page.getByRole("checkbox", { name: "Loneliness" }).focus();
  await page.keyboard.press(" ");
  await page.getByRole("checkbox", { name: "Sadness" }).focus();
  await page.keyboard.press(" ");
  await expect(page.getByTestId("chip-calm")).toHaveAttribute("data-selected", "true");
  await expect(page.getByTestId("chip-joy")).toBeDisabled(); // three is the cap
  await expect(page.getByTestId("chips-mood-emotions-cap")).toContainText("That is three");
  await page.getByRole("checkbox", { name: "Sleep" }).focus();
  await page.keyboard.press(" ");
  await expect(page.getByTestId("chip-sleep")).toHaveAttribute("data-selected", "true");

  // 4. The note, typed without touching the mouse.
  await page.getByLabel(/anything you want to add/i).focus();
  await page.keyboard.type(ORDINARY_NOTE);

  // 5. Save, with Enter.
  await page.getByRole("button", { name: "Save check-in" }).focus();
  await page.keyboard.press("Enter");

  // The confirmation replaces the form, is announced, and has focus.
  const confirmation = page.getByTestId("mood-confirmation");
  await expect(confirmation).toBeVisible();
  await expect(confirmation).toHaveAttribute("role", "status");
  await expect(page.getByRole("heading", { name: "Saved." })).toBeFocused();

  // One small action, and the streak line — never a reprimand.
  await expect(page.getByTestId("mood-suggested-action")).toBeVisible();
  await expect(page.getByTestId("mood-streak-headline")).toContainText(/saved|open|day/i);
  await expect(page.getByTestId("mood-streak")).not.toContainText(/broken|lost your|you missed/i);

  // The check-in is in the history below, without a reload.
  await expect(page.getByTestId("mood-history-entry").first()).toContainText(ORDINARY_NOTE);
});

test("a note that needs help returns the helplines and keeps the check-in", async ({ page }) => {
  await completeGuestOnboarding(page);

  await page.getByRole("radio", { name: "Very low" }).focus();
  await page.keyboard.press(" ");
  await page.getByLabel(/anything you want to add/i).focus();
  await page.keyboard.type(HIGH_RISK_NOTE);
  await page.getByRole("button", { name: "Save check-in" }).focus();
  await page.keyboard.press("Enter");

  const card = page.getByTestId("mood-crisis-card");
  await expect(card).toBeVisible();
  await expect(card.locator('a[href^="tel:"]').first()).toBeVisible();
  await expect(page.getByText(/Your check-in is saved/)).toBeVisible();
  await expect(page.getByTestId("mood-history-entry").first()).toContainText(HIGH_RISK_NOTE);
});

test("the daily reminder is stored on the device and says it is not sent", async ({ page }) => {
  await completeGuestOnboarding(page);

  const reminder = page.getByTestId("mood-reminder");
  await reminder.getByRole("checkbox", { name: "Remember to remind me" }).check();
  await reminder.getByLabel("Remind me at").fill("07:30");
  await expect(reminder.getByTestId("mood-reminder-status")).toContainText(
    "No reminders are sent yet",
  );

  await page.reload();
  await expect(page.getByTestId("mood-reminder").getByRole("checkbox")).toBeChecked();
});
