/**
 * Day 12 end-to-end: guest onboarding → send → streamed reply → crisis card.
 *
 * Run against the real backend with `LLM_PROVIDER=fake`:
 *
 *   # terminal 1 — the API (or: docker compose up, then E2E_API_URL=…)
 *   cd backend && LLM_PROVIDER=fake SECRET_KEY=dev-only FIELD_ENCRYPTION_KEY=… make dev
 *   # terminal 2
 *   cd frontend && npm run test:e2e
 *
 * What this spec is really checking, and why it cannot be a unit test:
 *
 * 1. **The SSE contract meets a browser.** The unit tests read a synthetic
 *    stream through a Vite-less client; here the reply crosses a real HTTP/1.1
 *    connection *and the Vite dev proxy*, which is where buffering would show.
 * 2. **The crisis gate is upstream of the model, in the running stack.** A
 *    high-risk message must produce the pre-written reply and the CrisisCard,
 *    and the Fake's default sentence must not appear anywhere.
 * 3. **The page is usable at 360px** and has no axe violations with a real
 *    conversation on screen.
 *
 * Backend settings this spec expects: `LLM_PROVIDER=fake` and
 * `SAFETY_ML_ENABLED=false`. The second is not a convenience — with the shipped
 * `crisis_v1` artifact the ML backstop raises ordinary messages to HIGH (see
 * PROGRESS.md → Known issues → Day 11 chat notes), which would answer "I had a
 * bad day" with a helpline card and fail the ordinary-message assertion below
 * for a reason that has nothing to do with this UI.
 */

import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/** A message the rules engine classifies HIGH (see `backend/app/content/safety`). */
const HIGH_RISK = "I want to kill myself";
/** An ordinary message: must be answered by the model, not by the crisis gate. */
const ORDINARY = "I had a bad day and I cannot switch my brain off";
/**
 * The Fake provider's unscripted reply (`DEFAULT_REPLY` in
 * `app/services/llm/fake_provider.py`). If this appears under a high-risk
 * message, the model answered something it must never answer.
 */
const FAKE_LLM_MARKER = "What has today been like for you?";

const ASSETS_DIR = fileURLToPath(new URL("../../docs/assets", import.meta.url));

/** Walk the five onboarding steps as a guest and land on /chat. */
async function completeGuestOnboarding(page: Page): Promise<void> {
  await page.goto("/");

  // 1-3: welcome, what Manovia is, the AI disclosure.
  for (let step = 0; step < 3; step += 1) {
    await page.getByRole("button", { name: "Continue", exact: true }).click();
  }

  // 4: the agreements. The three required ones must be ticked; `store_chat`
  // stays off, so this is an ephemeral conversation.
  await page.locator("#consent-terms").check();
  await page.locator("#consent-privacy").check();
  await page.locator("#consent-ai_disclosure").check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();

  // 5: how to start.
  await page.getByRole("button", { name: "Continue as a guest" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Chat" })).toBeVisible();
}

/** Send one message the way a person would: type it, press Enter. */
async function send(page: Page, text: string): Promise<void> {
  const box = page.getByRole("textbox", { name: /what is on your mind/i });
  await box.fill(text);
  await box.press("Enter");
  await expect(page.getByTestId("user-bubble").last()).toContainText(text);
}

test.beforeEach(async ({ page }) => {
  // Fail loudly and early if the API is not up: an e2e that quietly tests a
  // dead backend is worse than one that refuses to start.
  const response = await page.request.get("/api/v1/ready").catch(() => null);
  test.skip(response === null || !response.ok(), "the API is not reachable at /api/v1/ready");
  await completeGuestOnboarding(page);
});

test("guest onboarding, then a message that streams back a reply", async ({ page }) => {
  await expect(page.getByTestId("chat-empty")).toBeVisible();
  await expect(page.getByTestId("companion-notice")).toContainText("not a therapist");

  // A starter chip is reachable and works from the keyboard.
  await page.getByTestId("starter-chip").first().focus();
  await page.keyboard.press("Enter");

  // The typing indicator is up before any text arrives.
  await expect(page.getByTestId("typing-indicator")).toBeVisible();

  // Then the reply streams in and settles.
  const reply = page.getByTestId("assistant-text").first();
  await expect(reply).toBeVisible();
  await expect(reply).not.toHaveText("");

  // An ordinary message must reach the model, so the Fake's reply is expected —
  // and no crisis card must appear.
  await expect(reply).toContainText(FAKE_LLM_MARKER);
  await expect(page.getByTestId("chat-crisis-card")).toHaveCount(0);
  await expect(page.getByTestId("chat-view")).toHaveAttribute("data-softened", "false");
});

test("a HIGH-risk message shows the CrisisCard and the model is not heard from", async ({ page }) => {
  await send(page, ORDINARY);
  await expect(page.getByTestId("assistant-text").first()).toContainText(FAKE_LLM_MARKER);

  await send(page, HIGH_RISK);

  // The card is prominent, under the message, with one-tap contact actions.
  const card = page.getByTestId("chat-crisis-card");
  await expect(card).toBeVisible();
  await expect(card.locator('a[href^="tel:"]').first()).toBeVisible();

  // The reply is the pre-written crisis copy, not anything the model wrote.
  const latest = page.getByTestId("assistant-bubble").last();
  const cardTitle = (await card.locator("h2").first().innerText()).trim();
  expect(cardTitle.length).toBeGreaterThan(10);
  await expect(latest).toContainText(cardTitle);
  await expect(latest).not.toContainText(FAKE_LLM_MARKER);

  // The rest of the page softens, and the permanent disclosure line stays.
  await expect(page.getByTestId("chat-view")).toHaveAttribute("data-softened", "true");
  await expect(page.getByTestId("starter-chips")).toHaveCount(0);
  await expect(page.getByTestId("companion-notice")).toBeVisible();
});

test("keyboard only: Enter sends, Shift+Enter makes a new line", async ({ page }) => {
  const box = page.getByRole("textbox", { name: /what is on your mind/i });

  await box.focus();
  await page.keyboard.type("line one");
  await page.keyboard.press("Shift+Enter");
  await page.keyboard.type("line two");
  await expect(box).toHaveValue("line one\nline two");
  await expect(page.getByTestId("chat-message")).toHaveCount(0);

  await page.keyboard.press("Enter");
  await expect(page.getByTestId("user-bubble")).toContainText("line one");
});

test("the chat page has no axe violations with a conversation on screen", async ({ page }) => {
  await send(page, ORDINARY);
  await expect(page.getByTestId("assistant-text").first()).not.toHaveText("");

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

test("the crisis card page has no axe violations", async ({ page }) => {
  await send(page, HIGH_RISK);
  await expect(page.getByTestId("chat-crisis-card")).toBeVisible();

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

test("screenshots for docs/assets at this project's viewport", async ({ page }) => {
  const { name } = test.info().project;

  await send(page, ORDINARY);
  await expect(page.getByTestId("assistant-text").first()).not.toHaveText("");
  await page.screenshot({ path: `${ASSETS_DIR}/day12-chat-${name}.png`, fullPage: true });

  // Dark mode is a shipped palette, so it gets a screenshot too. (Settings owns
  // the preference; the attribute is what the palette actually keys off.)
  await page.evaluate(() => {
    document.documentElement.setAttribute("data-theme", "dark");
  });
  await page.screenshot({ path: `${ASSETS_DIR}/day12-chat-dark-${name}.png`, fullPage: true });
  await page.evaluate(() => {
    document.documentElement.removeAttribute("data-theme");
  });

  await send(page, HIGH_RISK);
  await expect(page.getByTestId("chat-crisis-card")).toBeVisible();
  await page.screenshot({ path: `${ASSETS_DIR}/day12-chat-crisis-${name}.png`, fullPage: true });
});

test("a dropped connection mid-stream says so instead of pretending", async ({ page }) => {
  // Kill the stream after the first chunk reaches the browser. The page must
  // keep the words that arrived and say the reply was cut off.
  await page.route("**/chat/sessions/*/stream", async (route) => {
    // One token, then the connection closes with no `final` event.
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: Buffer.from('event: token\ndata: {"text":"That sounds really hard. "}\n\n', "utf8"),
    });
  });

  await send(page, ORDINARY);

  await expect(page.getByTestId("chat-error")).toContainText("The reply was cut off");
  await expect(page.getByTestId("assistant-bubble").last()).toContainText("That sounds really hard.");
  await expect(page.getByTestId("chat-error-action")).toHaveText("Ask again");
});
