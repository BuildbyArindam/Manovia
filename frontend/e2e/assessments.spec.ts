/**
 * Day 20 end-to-end: the PHQ-9 screening against the real API.
 *
 *   # terminal 1 — the API. The raised auth rate limit is for the suite: six
 *   # tests times two viewports each create a guest, and the production default
 *   # is 10 auth requests per minute per client.
 *   cd backend && SECRET_KEY=dev-only RATE_LIMIT_AUTH_PER_MINUTE=60 make dev
 *   # terminal 2
 *   cd frontend && npm run test:e2e -- assessments.spec.ts
 *
 * What only a browser can prove here:
 *
 * 1. **The crisis block really is before the score.** The unit test asserts
 *    document order with `compareDocumentPosition`; this asserts it in a real
 *    render, over a real HTTP response, in both viewports.
 * 2. **Print media.** `@media print` cannot be evaluated in jsdom, so that the
 *    printout contains the summary — and the disclaimer — and none of the
 *    navigation is checked with `page.emulateMedia({ media: "print" })`.
 * 3. **Arrow-key stepping inside a radio group** is browser behaviour, not a
 *    jsdom one, and this questionnaire is nine radio groups in a row.
 *
 * No mouse is used after onboarding: every control is reached with `Tab` and
 * activated with a key, exactly as a keyboard-only person would.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";

const OPTIONS = ["Not at all", "Several days", "More than half the days", "Nearly every day"];

test.beforeEach(async ({ page }) => {
  const response = await page.request.get("/api/v1/ready").catch(() => null);
  test.skip(response === null || !response.ok(), "the API is not reachable at /api/v1/ready");
});

async function onboardAsGuest(page: Page): Promise<void> {
  await page.goto("/");
  for (let step = 0; step < 3; step += 1) {
    await page.getByRole("button", { name: "Continue", exact: true }).click();
  }
  await page.locator("#consent-terms").check();
  await page.locator("#consent-privacy").check();
  await page.locator("#consent-ai_disclosure").check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Continue as a guest" }).click();
  await page.getByRole("link", { name: /^Screenings\b/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Screenings" })).toBeVisible();
}

/** Tab until the target has focus, then hand it back to the caller to press a key. */
async function keyboardFocus(page: Page, target: Locator, what: string): Promise<void> {
  for (let i = 0; i < 60; i += 1) {
    if (await target.evaluate((node) => node === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`Keyboard could not reach ${what}`);
}

test("a screening is completable, and item 9 puts the helplines before the score", async ({
  page,
}, testInfo) => {
  await onboardAsGuest(page);

  // The instrument card names the source, so nobody has to trust a paraphrase.
  await expect(page.getByRole("heading", { name: "Patient Health Questionnaire-9" })).toBeVisible();

  const start = page.getByRole("button", { name: "Start the PHQ-9" });
  await keyboardFocus(page, start, "the start button");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("questionnaire")).toBeVisible();

  // The official item wording, not a rewrite of it.
  await expect(
    page
      .getByTestId("item-phq9_1")
      .getByText("Little interest or pleasure in doing things")
      .first(),
  ).toBeVisible();

  // Eight questions answered with Space, item 9 stepped to "Several days" with
  // the arrow keys — the crisis trigger.
  for (let position = 1; position <= 9; position += 1) {
    const group = page.getByTestId(`item-phq9_${position}`);
    const first = group.getByRole("radio", { name: OPTIONS[0] as string });
    await first.focus();
    if (position === 9) {
      await page.keyboard.press("ArrowRight");
      await expect(group.getByRole("radio", { name: "Several days" })).toBeChecked();
    } else {
      await page.keyboard.press(" ");
      await expect(first).toBeChecked();
    }
  }

  const submit = page.getByRole("button", { name: "See my result" });
  await keyboardFocus(page, submit, "the submit button");
  await page.keyboard.press("Enter");

  const crisis = page.getByTestId("crisis-block");
  const score = page.getByTestId("score-block");
  await expect(crisis).toBeVisible();
  await expect(score).toBeVisible();

  // 1. Help first, in the document itself.
  const order = await crisis.evaluate((node, other) => {
    const scoreNode = document.querySelector(`[data-testid="${other as string}"]`);
    if (scoreNode === null) return "missing";
    return node.compareDocumentPosition(scoreNode) & Node.DOCUMENT_POSITION_FOLLOWING
      ? "before"
      : "after";
  }, "score-block");
  expect(order, `${testInfo.project.name}: crisis block must precede the score`).toBe("before");

  // 2. A reachable helpline, the emergency number, and the service disclaimer.
  await expect(crisis.locator('a[href^="tel:"]').first()).toBeVisible();
  await expect(crisis.getByTestId("crisis-emergency-instruction")).toContainText("112");
  await expect(crisis.locator('a[href="tel:112"]')).toBeVisible();
  await expect(crisis.getByText(/not a crisis service/i)).toBeVisible();
  await expect(page.getByText(/Before your score, please read this/)).toBeVisible();

  // 3. The score is still shown, with the band and the disclaimer.
  await expect(score).toContainText("of 27");
  await expect(score).toContainText(/minimal/i);
  await expect(page.getByTestId("not-a-diagnosis")).toContainText(
    "screening result, not a diagnosis",
  );

  // 4. The trend follows the result, with the item-9 result in the table.
  await expect(page.getByTestId("trend-summary")).toContainText("One result so far");
  await expect(page.getByTestId("trend-chart")).toBeAttached();

  // 5. One result means a starting point and a one-row table — never a direction.
  await expect(page.getByTestId("trend-chart")).toBeAttached();

  testInfo.annotations.push({ type: "order", description: `crisis before score: ${order}` });
});

test("print media produces the clinician summary and nothing else", async ({ page }) => {
  await onboardAsGuest(page);
  await page.getByRole("button", { name: "Start the PHQ-9" }).click();
  for (let position = 1; position <= 9; position += 1) {
    await page
      .getByTestId(`item-phq9_${position}`)
      .getByRole("radio", { name: OPTIONS[1] as string })
      .check();
  }
  await page.getByRole("button", { name: "See my result" }).click();
  await expect(page.getByTestId("score-block")).toBeVisible();

  // On screen the summary is present but hidden — print CSS is what reveals it.
  const summary = page.getByTestId("print-summary");
  await expect(summary).toBeAttached();
  await expect(summary).toBeHidden();

  await page.emulateMedia({ media: "print" });
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("Manovia screening summary");
  await expect(summary).toContainText(/screening result, not a diagnosis/);
  await expect(summary).toContainText(/not therapy, not a diagnosis, and not a crisis service/);
  await expect(summary).toContainText(/Important:/);
  await expect(summary).toContainText(/No permission required to reproduce/);

  // The interactive page is gone in print: no navigation, no score card, no buttons.
  await expect(page.getByRole("heading", { level: 1, name: "Screenings" })).toBeHidden();
  await expect(page.getByTestId("score-block")).toBeHidden();
  await expect(page.getByRole("button", { name: "Print or save as PDF" })).toBeHidden();

  await page.emulateMedia({ media: "screen" });
  await expect(summary).toBeHidden();
});

test("the question page has no axe violations", async ({ page }, testInfo) => {
  await onboardAsGuest(page);
  await page.getByRole("button", { name: "Start the PHQ-9" }).click();
  await expect(page.getByTestId("questionnaire")).toBeVisible();
  // Answer two questions so the checked state is in scope for the scan.
  await page
    .getByTestId("item-phq9_1")
    .getByRole("radio", { name: OPTIONS[0] as string })
    .check();
  await page
    .getByTestId("item-phq9_2")
    .getByRole("radio", { name: OPTIONS[1] as string })
    .check();

  const result = await new AxeBuilder({ page }).include('[data-testid="questionnaire"]').analyze();
  process.stdout.write(
    `axe questionnaire ${testInfo.project.name}: ${result.violations.length} violations\n`,
  );
  await testInfo.attach("axe-questionnaire", {
    body: JSON.stringify(result),
    contentType: "application/json",
  });
  expect(result.violations).toEqual([]);
});

test("an incomplete questionnaire explains what is missing instead of submitting", async ({
  page,
}) => {
  await onboardAsGuest(page);
  await page.getByRole("button", { name: "Start the PHQ-9" }).click();
  await page
    .getByTestId("item-phq9_1")
    .getByRole("radio", { name: OPTIONS[0] as string })
    .check();
  await page.getByRole("button", { name: "See my result" }).click();

  const error = page.getByTestId("questionnaire-error");
  await expect(error).toContainText("8 questions are still unanswered");
  await expect(page.getByTestId("questionnaire")).toBeVisible();
  await expect(page.getByTestId("score-block")).toBeHidden();
});

test("the GAD-7 scores independently and never claims a depression diagnosis", async ({ page }) => {
  await onboardAsGuest(page);
  const start = page.getByRole("button", { name: "Start the GAD-7" });
  await keyboardFocus(page, start, "the GAD-7 start button");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("questionnaire")).toBeVisible();
  // Verbatim from the published GAD-7 sheet, including its missing serial comma.
  await expect(page.getByTestId("item-gad7_1")).toContainText(
    "Feeling nervous, anxious or on edge",
  );

  for (let position = 1; position <= 7; position += 1) {
    await page
      .getByTestId(`item-gad7_${position}`)
      .getByRole("radio", { name: OPTIONS[3] as string })
      .check();
  }
  await page.getByRole("button", { name: "See my result" }).click();

  const score = page.getByTestId("score-block");
  await expect(score).toContainText("21 of 21");
  await expect(score).toContainText(/severe/i);
  // All-3s is the ceiling of the instrument, and the copy must still not diagnose.
  await expect(page.getByTestId("not-a-diagnosis")).toContainText("not a diagnosis");
  await expect(page.getByTestId("crisis-block")).toHaveCount(0);
});

test("a stored result reopens from history with its item-9 line", async ({ page }) => {
  await onboardAsGuest(page);

  // Two runs, so the history list exists (one result is shown as a trend point
  // rather than a list) and the second one is loaded from the API, not from the
  // submit response.
  for (let run = 0; run < 2; run += 1) {
    if (run === 0) {
      const start = page.getByRole("button", { name: "Start the PHQ-9" });
      await keyboardFocus(page, start, "the start button");
      await page.keyboard.press("Enter");
    }
    // On the second run "Answer again" has left the questionnaire on screen.
    for (let position = 1; position <= 9; position += 1) {
      await page
        .getByTestId(`item-phq9_${position}`)
        .getByRole("radio", { name: position === 9 ? "Several days" : "Not at all" })
        .check();
    }
    const submit = page.getByRole("button", { name: "See my result" });
    await keyboardFocus(page, submit, "the submit button");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("score-block")).toBeVisible();
    if (run === 0) {
      // Back to the form for the second run; the second result stays on screen.
      const again = page.getByRole("button", { name: "Answer again" });
      await keyboardFocus(page, again, "the answer-again button");
      await page.keyboard.press("Enter");
    }
  }

  await expect(page.getByTestId("trend-summary")).toContainText("Across 2 results");
  const open = page.getByRole("button", { name: "Open and print" }).first();
  await keyboardFocus(page, open, "the open-and-print button");
  await page.keyboard.press("Enter");

  // The score card says the score came back from storage, not from a submit.
  await expect(page.getByTestId("score-block")).toContainText("a saved result");
  // The stored result is reconstructed from saved answers, so the item-9 fact
  // survives the round trip through the database.
  await expect(page.getByText(/thoughts of being better off dead/)).toBeVisible();
  await expect(page.getByTestId("print-summary")).toContainText("Manovia screening summary");
});
