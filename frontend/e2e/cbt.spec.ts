/** Day 19: real API + real Chromium; no mouse for the seven-step wizard. */
import { expect, test, type Page, type Locator } from "@playwright/test";

async function keyboardFocus(page: Page, target: Locator): Promise<void> {
  for (let i = 0; i < 40; i += 1) {
    if (await target.evaluate((node) => node === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error("Keyboard could not reach control");
}

async function enterText(page: Page, text: string): Promise<void> {
  await keyboardFocus(page, page.getByRole("textbox"));
  await page.keyboard.type(text);
  await keyboardFocus(page, page.getByRole("button", { name: "Next" }));
  await page.keyboard.press("Enter");
}

test("keyboard-only wizard displays before/after intensity and what helped", async ({ page }) => {
  expect((await page.request.get("/api/v1/ready")).ok()).toBe(true);
  await page.goto("/");
  for (let step = 0; step < 3; step += 1)
    await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.locator("#consent-terms").check();
  await page.locator("#consent-privacy").check();
  await page.locator("#consent-ai_disclosure").check();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Continue as a guest" }).click();
  await page.getByRole("link", { name: /^Thought record\b/ }).click();
  const start = page.getByRole("button", { name: "Start a reflection" });
  await keyboardFocus(page, start);
  await page.keyboard.press("Enter");
  await enterText(page, "My exam is tomorrow");
  await enterText(page, "I'll fail the exam and everyone will think I'm stupid");
  await expect(page.getByRole("heading", { name: "Possible thinking traps" })).toBeVisible();
  const before = page.getByLabel("Intensity before (0–100)");
  await keyboardFocus(page, before);
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("85");
  await keyboardFocus(page, page.getByRole("button", { name: "Next" }));
  await page.keyboard.press("Enter");
  await enterText(page, "I missed a practice question");
  await enterText(page, "I studied and passed practice exams");
  await enterText(page, "I prepared, even though I may not know every answer");
  const after = page.getByLabel("Intensity after (0–100)");
  await keyboardFocus(page, after);
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("45");
  await keyboardFocus(page, page.getByRole("checkbox", { name: "looking at evidence" }));
  await page.keyboard.press("Space");
  await keyboardFocus(page, page.getByRole("button", { name: "Save reflection" }));
  await page.keyboard.press("Enter");
  await expect(page.getByText("anxiety: before 85/100 → after 45/100")).toBeVisible();
  await expect(page.getByText("What helped: looking at evidence")).toBeVisible();
});
