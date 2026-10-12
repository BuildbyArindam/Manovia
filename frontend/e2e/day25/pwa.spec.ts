/**
 * Day 25 — "PWA pass", measured in the browser.
 *
 * Lighthouse 12 removed its PWA category, so `lighthouserc.json` cannot assert
 * installability. This spec checks it directly instead:
 *
 * * **Chromium's own manifest parser** (`Page.getAppManifest` over the
 *   DevTools protocol — the same parser the install flow uses) must find the
 *   manifest with no errors, and its *parsed* result must meet Chrome's
 *   documented install criteria: a name, a start URL inside the scope, a
 *   standalone display mode, 192px and 512px icons, no preferred native app.
 * * Every icon is fetched and its real pixel size read from the PNG header.
 * * The page is a secure context, a service worker controls it, and the start
 *   URL opens with the network switched off.
 *
 * `Page.getInstallabilityErrors` is deliberately *not* used: in the headless
 * Chromium this runs on it returned an empty list even for a page with no
 * manifest at all (checked on 2026-10-12), so an empty list proves nothing.
 * The negative control at the end keeps the parser check honest the same way:
 * a broken manifest must produce parser errors.
 */

import { expect, test, type Page } from "@playwright/test";

interface ParsedManifest {
  url: string;
  errors: Array<{ message: string; critical: number }>;
  manifest?: {
    name?: string;
    shortName?: string;
    display?: string;
    startUrl?: string;
    scope?: string;
    preferRelatedApplications?: boolean;
    icons?: Array<{ url: string; sizes?: string; type?: string }>;
    shortcuts?: Array<{ name: string; url: string }>;
  };
}

function pngSize(bytes: Buffer): { width: number; height: number } {
  expect(bytes.subarray(1, 4).toString("ascii")).toBe("PNG");
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

async function waitForWorker(page: Page): Promise<void> {
  await page.waitForFunction(
    async () => {
      await navigator.serviceWorker.ready;
      return navigator.serviceWorker.controller !== null;
    },
    undefined,
    { timeout: 30_000 },
  );
}

test.beforeEach(() => {
  test.skip(
    test.info().project.name !== "desktop-1280x800",
    "installability does not depend on the viewport",
  );
});

test("the manifest parses cleanly and meets the install criteria", async ({ page, context }) => {
  await page.goto("/");
  await waitForWorker(page);
  expect(await page.evaluate(() => window.isSecureContext)).toBe(true);

  const cdp = await context.newCDPSession(page);
  const result = (await cdp.send("Page.getAppManifest")) as ParsedManifest;
  expect(result.url).toMatch(/\/manifest\.webmanifest$/);
  expect(result.errors.map((error) => error.message)).toEqual([]);

  const parsed = result.manifest ?? {};
  const origin = new URL(page.url()).origin;
  expect(parsed.name).toBe("Manovia");
  expect(parsed.display).toBe("kStandalone");
  expect(parsed.startUrl).toBe(`${origin}/`);
  expect(parsed.scope).toBe(`${origin}/`);
  expect(parsed.startUrl?.startsWith(parsed.scope ?? "\u0000")).toBe(true);
  expect(parsed.preferRelatedApplications).toBe(false);

  const icons = parsed.icons ?? [];
  expect(icons.some((icon) => icon.sizes === "192x192" && icon.type === "image/png")).toBe(true);
  expect(icons.some((icon) => icon.sizes === "512x512" && icon.type === "image/png")).toBe(true);
  for (const icon of icons) {
    const response = await page.request.get(icon.url);
    expect(response.status(), icon.url).toBe(200);
    expect(response.headers()["content-type"]).toContain("image/png");
    const [width, height] = (icon.sizes ?? "0x0").split("x").map(Number);
    expect(pngSize(await response.body()), icon.url).toEqual({ width, height });
  }
  const apple = await page.request.get("/icons/apple-touch-icon-180.png");
  expect(pngSize(await apple.body())).toEqual({ width: 180, height: 180 });

  // The raw manifest declares a maskable icon (Chromium's parse omits purpose).
  const raw = (await (await page.request.get("/manifest.webmanifest")).json()) as {
    icons: Array<{ purpose?: string }>;
  };
  expect(raw.icons.some((icon) => icon.purpose === "maskable")).toBe(true);

  // Shortcuts point at real places, and "Need help now?" opens the helplines.
  const help = parsed.shortcuts?.find((shortcut) => shortcut.name === "Need help now?");
  expect(help?.url).toBe(`${origin}/?help=1`);
});

test("the 'Need help now?' shortcut opens the helplines and cleans the address", async ({
  page,
}) => {
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

  await page.goto("/?help=1");
  await expect(page.getByTestId("crisis-modal")).toBeVisible();
  await expect(page.getByTestId("crisis-modal").locator('a[href^="tel:"]').first()).toBeVisible();
  expect(new URL(page.url()).search).toBe("");
});

test("the start URL opens offline once the worker is installed", async ({ page, context }) => {
  await page.goto("/");
  await waitForWorker(page);
  await context.setOffline(true);
  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  expect(response?.fromServiceWorker()).toBe(true);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await context.setOffline(false);
});

test("negative control: a broken manifest is reported by the same parser", async ({
  page,
  context,
}) => {
  await page.goto("/accessibility");
  await page.setContent(
    '<html><head><link rel="manifest" href="/missing.webmanifest"></head><body></body></html>',
  );
  const cdp = await context.newCDPSession(page);
  await expect
    .poll(async () => ((await cdp.send("Page.getAppManifest")) as ParsedManifest).errors.length)
    .toBeGreaterThan(0);
});
