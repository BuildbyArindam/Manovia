/**
 * Day 45 end-to-end: safety preface, the "stop instantly" control, and the
 * eyes-open / driving variants — in a real browser.
 *
 * Run against the real backend with the fake voice provider (no LLM is
 * involved in this path):
 *
 *   # terminal 1 — the API
 *   cd backend && LLM_PROVIDER=fake make dev
 *   # terminal 2
 *   cd frontend && npx playwright test e2e/meditation.spec.ts
 *
 * Why this cannot be a jsdom test:
 *
 * 1. **Audio playback is a browser fact.** The "Stop now" button only exists
 *    while the rendered WAV is actually playing; jsdom has no media pipeline,
 *    so the playing state — and the stop control inside it — is only
 *    observable here.
 * 2. **The cross-origin render round trip is the contract.** compose →
 *    render → captions crosses the Vite dev proxy to a real FastAPI server
 *    with the real content-hash gate, and comes back as bytes a real
 *    `<audio>` element decodes — the contract, not a mock.
 *
 * What the sandbox could not run: the sandbox has no Chromium (Playwright's
 * CDN is outside the egress allowlist), so this spec is run on a machine
 * with `npx playwright install chromium`. The safety *content* (preface
 * chips, stop cue, eyes-open text) is additionally covered by
 * `MeditationView.test.tsx` (jsdom) and the API e2e
 * (`evals/meditation/verify_api_e2e.py`), so a green jsdom suite plus a
 * green API e2e already bounds what this spec re-checks in a browser.
 */

import { expect, test, type Page } from "@playwright/test";

/** Walk the guest onboarding and land on the meditation page. */
async function toMeditation(page: Page): Promise<void> {
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
  await page.goto("/meditation");
  await expect(page.getByRole("heading", { level: 1, name: "Meditations" })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  const response = await page.request.get("/api/v1/ready").catch(() => null);
  test.skip(response === null || !response.ok(), "the API is not reachable at /api/v1/ready");
});

test("the quick action composes a session that carries the safety preface and a stop cue", async ({
  page,
}) => {
  await toMeditation(page);

  await page.getByRole("button", { name: "Make me a 3-minute one for right now" }).click();

  // The session section fills with the composed script.
  const transcript = page.getByTestId("meditation-transcript");
  await expect(transcript).toBeVisible();
  await expect(page.getByTestId("section-stop")).toBeVisible();

  // Safety preface chips: the session is self-guided and can be stopped.
  const safety = page.getByLabel(/safety notes/i);
  await expect(safety.getByText("Self-guided — not a session with a practitioner")).toBeVisible();
  await expect(safety.getByText("You can stop at any time")).toBeVisible();

  // The spoken text itself carries the stop cue (not just the chip).
  await expect(page.getByTestId("section-stop")).toContainText("stop at any time");
});

test("playing shows a stop-now control and stopping returns to idle instantly", async ({ page }) => {
  await toMeditation(page);
  await page.getByRole("button", { name: "Make me a 3-minute one for right now" }).click();
  await expect(page.getByTestId("meditation-transcript")).toBeVisible();

  // Render + play the real WAV (fake provider). Headless Chromium plays
  // audio silently; the playing state is what puts "Stop now" on screen.
  await page.getByTestId("play-soundscape").click();
  const stopNow = page.getByTestId("stop-now");
  await expect(stopNow).toBeVisible();

  // Stop is instant: the moment it is pressed the playing UI is gone and
  // the play buttons are back — no timer, no confirmation.
  await stopNow.click();
  await expect(stopNow).not.toBeVisible();
  await expect(page.getByTestId("play-soundscape")).toBeEnabled();
  await expect(page.getByTestId("play-voice")).toBeEnabled();
});

test("the eyes-open variant is offered and honoured", async ({ page }) => {
  await toMeditation(page);

  await page.getByRole("checkbox", { name: /eyes-open/i }).check();
  await page.getByRole("button", { name: "Make this meditation" }).click();
  await expect(page.getByTestId("meditation-transcript")).toBeVisible();

  await expect(page.getByLabel(/safety notes/i).getByText(/eyes open/i)).toBeVisible();
});

test("the driving context carries the road-safety note and no eyes-closed instruction", async ({
  page,
}) => {
  await toMeditation(page);

  const context = page.getByLabel(/where are you right now/i);
  await context.selectOption({ label: "Driving" });
  await page.getByRole("button", { name: "Make this meditation" }).click();
  await expect(page.getByTestId("meditation-transcript")).toBeVisible();

  // The warning chip is shown…
  await expect(
    page.getByLabel(/safety notes/i).getByText("Please stay focused on the road"),
  ).toBeVisible();
  // …and the spoken text never instructs closing the eyes.
  const spoken = (await page.getByTestId("meditation-transcript").innerText()).toLowerCase();
  expect(spoken).not.toContain("close your eyes");
  expect(spoken).not.toContain("eyes closed");
});
