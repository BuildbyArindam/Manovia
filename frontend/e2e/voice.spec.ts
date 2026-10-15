import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.describe("Voice Journaling (Day 39)", () => {
  test("Voice recorder is keyboard accessible and axe-clean", async ({ page }) => {
    await page.goto("/chat");
    
    // Accept consents if they appear
    if (await page.getByText("I agree", { exact: false }).count() > 0) {
      await page.getByText("I agree", { exact: false }).first().click();
    }
    
    const axe = new AxeBuilder({ page });
    const results = await axe.analyze();
    expect(results.violations).toEqual([]);

    // Keyboard navigate to record button
    await page.getByRole("button", { name: /voice journal/i }).focus();
    await expect(page.getByRole("button", { name: /voice journal/i })).toBeFocused();
    
    // We don't click it because of permissions, but we can verify it's there
    await expect(page.getByRole("button", { name: /voice journal/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /voice journal/i })).toHaveAttribute("title", expect.stringContaining("NEVER stored"));
  });
});
