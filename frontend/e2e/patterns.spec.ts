import { expect, test, type Locator, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFile } from "node:fs/promises";
import { synthetic } from "../src/features/patterns/fixtures";
function seed(): void {
  localStorage.setItem(
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
const SENTINEL = "D43_PRIVATE_JOURNAL_MARKER";
const data = {
  version: 1,
  days: synthetic(0).map((d) => ({ ...d, journal: `${d.journal} ${SENTINEL}` })),
};
async function importFile(page: Page, value: unknown): Promise<void> {
  await page.getByLabel("Import local data or resume experiment").setInputFiles({
    name: "local-patterns.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(value)),
  });
}
async function tabTo(page: Page, locator: Locator): Promise<void> {
  for (let i = 0; i < 160; i++) {
    if (await locator.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error("Keyboard could not reach target");
}
test("Private Mode: no network egress, themes, keyboard graph/list, axe, export/resume and delete", async ({
  page,
}) => {
  await page.addInitScript(seed);
  await page.addInitScript(() => localStorage.setItem("manovia.private.enabled.v1", "1"));
  const api: string[] = [],
    outbound: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/")) api.push(`${r.method()} ${r.url()}`);
    outbound.push(`${r.url()} ${r.postData() ?? ""}`);
  });
  await page.route("**/api/**", (route) => route.abort());
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/patterns");
  await expect(page.getByRole("heading", { name: "Personal patterns", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Read my existing history" })).toBeDisabled();
  await importFile(page, data);
  await expect(
    page.getByRole("status").filter({ hasText: "120 observed days loaded" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Discover themes locally" }).click();
  await expect(page.getByLabel("Theme name")).toHaveCount(2);
  const themeName = page.getByLabel("Theme name").first();
  await themeName.press("ControlOrMeta+A");
  await themeName.press("Backspace");
  await expect(themeName).toHaveValue("");
  await themeName.pressSequentially("Outdoors");
  await page.getByLabel("Theme name").last().fill("Reading");
  const graph = page.getByRole("group", { name: "Interactive pattern graph" });
  const first = graph.getByRole("button").first();
  await tabTo(page, first);
  await page.keyboard.press("ArrowRight");
  await expect(graph.getByRole("button").nth(1)).toBeFocused();
  await page.keyboard.press("End");
  await expect(graph.getByRole("button").last()).toBeFocused();
  await page.keyboard.press("Home");
  await expect(first).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(first).toHaveAttribute("aria-pressed", "true");
  const textButton = page.getByRole("button", { name: "A walk (activity)", exact: true });
  await tabTo(page, textButton);
  await page.keyboard.press("Space");
  await expect(page.getByLabel("Choose a factor")).toHaveValue("walk");
  await expect(page.getByRole("table")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await tabTo(page, page.getByLabel("Text list only"));
  await page.keyboard.press("Space");
  await expect(graph).toHaveCount(0);
  await tabTo(page, textButton);
  await page.keyboard.press("Enter");
  await expect(textButton).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByText("This does not prove cause.", { exact: false }).first(),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Randomise 14 days" }).click();
  await expect(page.getByText("Optional activity day", { exact: false })).toHaveCount(7);
  for (const input of await page.getByLabel(/^Mood for /).all()) await input.selectOption("3");
  await expect(
    page.getByRole("status").filter({ hasText: "No clear difference was observed" }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export graph / experiment" }).click();
  const download = await downloadPromise,
    path = await download.path();
  const exported = JSON.parse(await readFile(path!, "utf8"));
  expect(exported.graph.edges.length).toBeGreaterThanOrEqual(2);
  expect(JSON.stringify(exported)).not.toContain(SENTINEL);
  expect(exported.themes[0].name).toBe("Outdoors");
  expect(exported.experiment.assigned).toHaveLength(7);
  await page.getByRole("button", { name: "Delete local graph and experiment" }).click();
  await expect(page.getByRole("button", { name: "Randomise 14 days" })).toBeVisible();
  await expect(page.getByLabel("Theme name")).toHaveCount(0);
  await expect(page.getByRole("table")).toHaveCount(0);
  await importFile(page, exported);
  await expect(
    page.getByRole("status").filter({ hasText: "No clear difference was observed" }),
  ).toBeVisible();
  await page.getByLabel("Merge Outdoors into").selectOption(exported.themes[1].id);
  await expect(page.getByLabel("Theme name")).toHaveCount(1);
  await page.getByRole("button", { name: "Delete Reading", exact: true }).click();
  await expect(page.getByLabel("Theme name")).toHaveCount(0);
  expect(api).toEqual([]);
  expect(
    outbound.some((r) => r.includes(SENTINEL) || r.includes('"assigned"') || r.includes('"graph"')),
  ).toBe(false);
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(storage).not.toContain(SENTINEL);
  expect(storage).not.toContain("Outdoors");
  await page.reload();
  await expect(page.getByRole("button", { name: "Randomise 14 days" })).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  console.warn(
    `Privacy inspection: ${api.length} API requests; no derived payloads; axe 0 violations (graph + text + completed experiment).`,
  );
});
test("minimum sample refuses all what-if numbers", async ({ page }) => {
  await page.addInitScript(seed);
  await page.route("**/api/**", (route) => route.abort());
  await page.goto("/patterns");
  await importFile(page, { version: 1, days: data.days.slice(0, 4) });
  await expect(page.getByText("Not enough days to compare.", { exact: false })).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
});
test("explicit existing-source reads only; derived data never sent", async ({ page }) => {
  await page.addInitScript(seed);
  const api: Array<{ method: string; url: string; body: string | null }> = [];
  await page.route("**/api/**", async (route) => {
    const r = route.request();
    api.push({ method: r.method(), url: r.url(), body: r.postData() });
    const path = new URL(r.url()).pathname;
    if (path.endsWith("/mood"))
      return route.fulfill({
        json: {
          entries: data.days.map((d) => ({
            local_date: d.date,
            valence: d.mood,
            factors: d.factors.filter((f) => f.kind === "factor").map((f) => f.label),
            emotions: [],
          })),
        },
      });
    if (path.endsWith("/journal"))
      return route.fulfill({
        json: {
          items: data.days.map((d) => ({ created_at: `${d.date}T12:00:00`, body: d.journal })),
          has_more: false,
        },
      });
    if (path.endsWith("/exercises/history"))
      return route.fulfill({ json: { items: [], has_more: false } });
    return route.abort();
  });
  await page.goto("/patterns");
  await page.getByRole("button", { name: "Read my existing history" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "120 observed days loaded" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Discover themes locally" }).click();
  await page.getByLabel("Theme name").first().fill("Private renamed topic");
  await page.getByRole("button", { name: "Randomise 14 days" }).click();
  expect(api).toHaveLength(3);
  expect(
    api.every(
      (r) =>
        r.method === "GET" &&
        r.body === null &&
        !r.url.includes(SENTINEL) &&
        !r.url.includes("Private"),
    ),
  ).toBe(true);
  console.warn("Standard mode inspection:", api);
});
