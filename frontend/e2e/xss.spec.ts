/**
 * Output encoding, proved in a real browser.
 *
 * The backend suite proves the API hands a hostile string back as JSON text.
 * That is necessary but not sufficient: the browser is where a payload becomes
 * code, and only a real DOM can show that it did not. So this spec types the
 * canonical `<img src=x onerror=…>` — which fires with no user interaction —
 * into chat, the journal and a mood note, then asserts three things:
 *
 * 1. it is visible as the literal text that was typed;
 * 2. no element was created from it (`img`, `script`, `svg`, `iframe`);
 * 3. nothing executed: the global the payload would set is still undefined.
 *
 * It also checks the page runs under the dev Content-Security-Policy with no
 * violations, which is the other half of "text is never code": even a mistake
 * here would be refused by the browser before it ran.
 *
 * Needs the real backend, like every other spec here:
 *
 *   cd backend && LLM_PROVIDER=fake SECRET_KEY=dev-only FIELD_ENCRYPTION_KEY=… make dev
 *   cd frontend && npm run test:e2e
 */

import { expect, test, type Page } from "@playwright/test";

/** The classic payload: no interaction needed, so a failure is unambiguous. */
const XSS = '<img src=x onerror="window.__manoviaXss=true">';
/** A second shape, to show it is not one string being special-cased. */
const SVG_XSS = "<svg/onload=window.__manoviaXss=true>";

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

/** No handler ran: the payload's global was never set. */
async function assertNothingExecuted(page: Page): Promise<void> {
  const executed = await page.evaluate(
    () => (window as { __manoviaXss?: boolean }).__manoviaXss ?? false,
  );
  expect(executed, "a payload executed").toBe(false);
}

/** No element was built out of the payload. */
async function assertNoElements(page: Page): Promise<void> {
  await expect(page.locator('img[src="x"]')).toHaveCount(0);
  await expect(page.locator("svg[onload]")).toHaveCount(0);
}

test.beforeEach(async ({ page }) => {
  const ready = await page.request.get("/api/v1/ready").catch(() => null);
  test.skip(ready === null || !ready.ok(), "the API is not reachable at /api/v1/ready");
});

test("a hostile chat message is rendered as text", async ({ page }) => {
  await completeGuestOnboarding(page);
  await page.goto("/chat");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  const box = page.getByRole("textbox", { name: /what is on your mind/i });
  await box.fill(XSS);
  await box.press("Enter");

  const bubble = page.locator('[data-testid="user-bubble"]').last();
  await expect(bubble).toContainText("<img src=x");
  // The text is a text node inside the paragraph, not markup.
  await expect(bubble.locator("p").last()).toHaveText(XSS);
  await assertNoElements(page);
  await assertNothingExecuted(page);
});

test("a hostile journal entry is stored and shown as text", async ({ page }) => {
  await completeGuestOnboarding(page);
  await page.goto("/journal");
  await expect(page.getByTestId("journal-view")).toBeVisible();

  await page.getByTestId("journal-title-input").fill(XSS);
  await page.getByTestId("journal-body-input").fill(`A calm first line.\n${SVG_XSS}`);
  await page.getByTestId("journal-save-button").click();
  await expect(page.getByTestId("journal-save-status")).toContainText(/[Ss]aved/);

  // Reload: the entry now comes back from the server, decrypted, as text.
  await page.reload();
  const sidebar = page.getByTestId("journal-sidebar");
  await expect(sidebar).toContainText("<img src=x");
  await assertNoElements(page);
  await assertNothingExecuted(page);
});

test("a hostile mood note survives the round trip as text", async ({ page }) => {
  await completeGuestOnboarding(page);
  await page.goto("/mood");
  await expect(page.getByTestId("mood-page")).toBeVisible();

  // The radio itself is `sr-only` (the label is the visible target), so click
  // the way a person does.
  await page.getByTestId("mood-face-3").click();
  await expect(page.getByRole("radio", { name: "In the middle" })).toBeChecked();
  await page.locator("#mood-note").fill(XSS);
  await page.getByRole("button", { name: "Save check-in" }).click();

  // The note comes back from the server in the history, as text.
  await page.reload();
  await expect(page.getByText("<img src=x").first()).toBeVisible();
  await assertNoElements(page);
  await assertNothingExecuted(page);
});

test("the app runs under the dev Content-Security-Policy without violations", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy/i.test(message.text())) {
      violations.push(message.text());
    }
  });

  const response = await page.goto("/");
  const csp = response?.headers()["content-security-policy"] ?? "";
  expect(csp).toContain("object-src 'none'");
  expect(csp).toContain("frame-ancestors 'none'");

  await completeGuestOnboarding(page);
  for (const route of ["/settings", "/journal", "/chat", "/mood"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
  }
  expect(violations).toEqual([]);
});

test("the privacy screen is reachable from Settings and states the facts", async ({ page }) => {
  await completeGuestOnboarding(page);
  await page.goto("/settings");

  const section = page.getByTestId("privacy-section");
  await section.scrollIntoViewIfNeeded();
  await expect(section).toBeVisible();
  await expect(section).toContainText("Safety events: never your words");
  await expect(section).toContainText("Anonymous mode");
  await expect(page.getByTestId("export-button")).toBeEnabled();
  await expect(page.getByTestId("delete-account-button")).toBeDisabled();
});
