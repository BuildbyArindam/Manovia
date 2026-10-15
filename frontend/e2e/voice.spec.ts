import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.describe("Real-time voice companion (Day 40)", () => {
  test("voice controls are keyboard accessible and do not need a microphone to inspect", async ({
    page,
  }) => {
    await page.goto("/chat");

    // Accept consent if onboarding is present. The test deliberately never
    // starts capture: browser/audio-device permissions are a manual-device
    // check, while these visible controls must work without them.
    const agree = page.getByText("I agree", { exact: false }).first();
    if (await agree.isVisible()) {
      await agree.click();
    }

    const companion = page.getByTestId("voice-companion");
    await expect(companion).toBeVisible();
    await expect(companion).toContainText(
      "Live audio and the transcript stay in this session only",
    );

    const start = page.getByTestId("voice-start");
    await start.focus();
    await expect(start).toBeFocused();
    await expect(start).toHaveText("Start voice conversation");

    const transcriptToggle = page.getByTestId("toggle-transcript");
    await transcriptToggle.focus();
    await page.keyboard.press("Space");
    await expect(page.getByTestId("voice-transcript")).toBeHidden();
    await page.keyboard.press("Space");
    await expect(page.getByTestId("voice-transcript")).toBeVisible();

    const mute = page.getByTestId("mute-assistant");
    await mute.focus();
    await page.keyboard.press("Space");
    await expect(mute).toBeChecked();

    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
});
