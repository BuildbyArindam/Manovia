/**
 * Day 25 — axe-core on every route and the states that matter, plus a 44px
 * target-size audit, in a real browser against the production build.
 *
 * Scope of each scan: WCAG 2.0/2.1/2.2 A and AA rules plus axe best practices.
 * The gate is **zero serious or critical violations**; everything axe reports
 * (moderate and minor included) is written to `test-results/day25/` so the
 * verification document quotes the real output rather than a summary of it.
 *
 * Appearances: light and dark at standard contrast, and both palettes in the
 * high-contrast mode — a palette that only passes in one theme is a palette
 * that fails somebody.
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

import {
  APP_ROUTES,
  apiReachable,
  applyAppearance,
  completeGuestOnboarding,
  openRoute,
  waitForOfflineReady,
} from "./helpers";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];
const BLOCKING = new Set(["serious", "critical"]);
const MIN_TARGET = 44;

const APPEARANCES = [
  { theme: "light", contrast: "standard" },
  { theme: "dark", contrast: "standard" },
  { theme: "light", contrast: "more" },
  { theme: "dark", contrast: "more" },
] as const;

interface ScanRecord {
  project: string;
  surface: string;
  appearance: string;
  violations: Array<{
    id: string;
    impact: string | null;
    nodes: number;
    help: string;
    targets: string[];
  }>;
}

/**
 * Records are appended as JSON lines as they happen: Playwright restarts the
 * worker after a failing test, so module state cannot hold a whole run.
 * `npm run test:e2e:prod` starts from an empty `test-results/day25/`.
 */
function record(info: TestInfo, kind: "scan" | "target", data: object): void {
  const file = `test-results/day25/axe-${info.project.name}.jsonl`;
  mkdirSync(dirname(file), { recursive: true });
  appendFileSync(file, `${JSON.stringify({ kind, project: info.project.name, ...data })}\n`);
}

async function scan(
  page: Page,
  info: TestInfo,
  surface: string,
  appearance: string,
): Promise<void> {
  const result = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const entry: Omit<ScanRecord, "project"> = {
    surface,
    appearance,
    violations: result.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact ?? null,
      nodes: violation.nodes.length,
      help: violation.help,
      targets: violation.nodes.slice(0, 5).map((node) => node.target.join(" ")),
    })),
  };
  record(info, "scan", entry);
  const blocking = result.violations.filter((violation) => BLOCKING.has(violation.impact ?? ""));
  // Soft: one run reports every surface, not just the first that fails.
  expect
    .soft(
      blocking.map(
        (violation) =>
          `${violation.id} (${violation.impact}): ${violation.nodes.map((node) => node.target.join(" ")).join(", ")}`,
      ),
      `${surface} [${appearance}] serious/critical axe violations`,
    )
    .toEqual([]);
}

/**
 * Every visible interactive element's target box. A visually hidden native
 * radio or checkbox is measured by its label (the thing a finger actually
 * hits); a link inside a run of text is exempt (WCAG 2.5.8 "inline").
 */
async function auditTargets(page: Page, info: TestInfo, surface: string): Promise<void> {
  const failures = await page.evaluate((minimum) => {
    const selector = [
      "a[href]",
      "button",
      "input:not([type=hidden])",
      "select",
      "textarea",
      "summary",
      "[role=button]",
      "[role=link]",
      "[role=checkbox]",
      "[role=radio]",
      "[role=switch]",
      "[role=tab]",
    ].join(",");
    const out: Array<{ element: string; width: number; height: number }> = [];
    const describe = (element: Element): string => {
      const label =
        element.getAttribute("aria-label") ??
        (element.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 40);
      const id = element.id ? `#${element.id}` : "";
      return `${element.tagName.toLowerCase()}${id} "${label}"`;
    };
    for (const element of Array.from(document.querySelectorAll(selector))) {
      const style = window.getComputedStyle(element);
      if (style.visibility === "hidden" || style.display === "none") {
        continue;
      }
      if (element.closest("[aria-hidden=true], .skip-link")) {
        continue;
      }
      let box = element.getBoundingClientRect();
      const input = element as HTMLInputElement;
      const isChoice =
        element.tagName === "INPUT" && (input.type === "radio" || input.type === "checkbox");
      if (isChoice) {
        const label =
          (input.id ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`) : null) ??
          input.closest("label");
        if (label !== null) {
          box = label.getBoundingClientRect();
        }
      }
      if (box.width === 0 && box.height === 0) {
        continue; // not rendered (e.g. the sr-only live region's children)
      }
      if (element.tagName === "A") {
        const parent = element.parentElement;
        const inline =
          parent !== null &&
          ["P", "LI", "SPAN", "DD"].includes(parent.tagName) &&
          (parent.textContent ?? "").trim().length > (element.textContent ?? "").trim().length + 1;
        if (inline) {
          continue;
        }
      }
      if (box.width < minimum - 0.5 || box.height < minimum - 0.5) {
        out.push({
          element: describe(element),
          width: Math.round(box.width),
          height: Math.round(box.height),
        });
      }
    }
    return out;
  }, MIN_TARGET);
  for (const failure of failures) {
    record(info, "target", { surface, ...failure });
  }
  expect.soft(failures, `${surface}: interactive targets under ${MIN_TARGET}px`).toEqual([]);
}

test.beforeEach(async ({ page }) => {
  test.skip(!(await apiReachable(page)), "the API is not reachable at /api/v1/ready");
});

test("onboarding and the pre-onboarding statement have no serious axe violations", async ({
  page,
}, info) => {
  await page.goto("/");
  for (let step = 1; step <= 5; step += 1) {
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await scan(page, info, `onboarding step ${step}`, "light/standard");
    await auditTargets(page, info, `onboarding step ${step}`);
    if (step === 4) {
      await page.locator("#consent-terms").check();
      await page.locator("#consent-privacy").check();
      await page.locator("#consent-ai_disclosure").check();
    }
    if (step < 5) {
      await page.getByRole("button", { name: "Continue", exact: true }).click();
    }
  }
  await page.goto("/accessibility");
  await expect(page.getByTestId("accessibility-page")).toBeVisible();
  await scan(page, info, "accessibility (before onboarding)", "light/standard");
});

for (const appearance of APPEARANCES) {
  const label = `${appearance.theme}/${appearance.contrast}`;
  test(`every route, ${label}`, async ({ page }, info) => {
    await completeGuestOnboarding(page);
    await applyAppearance(page, appearance);
    for (const route of APP_ROUTES) {
      await openRoute(page, route.path, route.ready);
      await expect(page.locator("html")).toHaveAttribute("data-theme", appearance.theme);
      if (appearance.contrast === "more") {
        await expect(page.locator("html")).toHaveAttribute("data-contrast", "more");
      }
      await scan(page, info, route.name, label);
      if (appearance.theme === "light" && appearance.contrast === "standard") {
        await auditTargets(page, info, route.name);
      }
    }
  });
}

test("states: help dialog, exercise runner, mood confirmation, offline", async ({
  page,
  context,
}, info) => {
  await completeGuestOnboarding(page);
  await waitForOfflineReady(page);

  await page.getByRole("button", { name: "Need help now?" }).first().click();
  await expect(page.getByTestId("crisis-modal").locator('a[href^="tel:"]').first()).toBeVisible();
  await scan(page, info, "help dialog", "light/standard");
  await auditTargets(page, info, "help dialog");
  await page.keyboard.press("Escape");

  await openRoute(page, "/exercises", "exercises-page");
  await page.getByRole("button", { name: "Try Box breathing" }).click();
  await scan(page, info, "exercise runner (before start)", "light/standard");
  await page.getByRole("button", { name: "Start exercise" }).click();
  await expect(page.getByRole("timer")).toBeVisible();
  await scan(page, info, "exercise runner (running)", "light/standard");
  await auditTargets(page, info, "exercise runner (running)");
  await page.getByRole("button", { name: "Stop exercise" }).click();

  await openRoute(page, "/mood", "mood-page");
  await page.getByRole("radio", { name: "Very low" }).focus();
  await page.keyboard.press(" ");
  await page.getByRole("button", { name: "Save check-in" }).click();
  await expect(page.getByTestId("mood-confirmation")).toBeVisible();
  await scan(page, info, "mood confirmation", "light/standard");

  await context.setOffline(true);
  await page.reload();
  await expect(page.getByTestId("offline-banner")).toBeVisible();
  // The reload brought back a fresh form (the confirmation lives in page state).
  await page.getByRole("radio", { name: "Very low" }).focus();
  await page.keyboard.press(" ");
  await page.getByRole("button", { name: "Save check-in" }).click();
  await expect(page.getByTestId("mood-queued-confirmation")).toBeVisible();
  await scan(page, info, "mood queued offline + banner", "light/standard");
  await auditTargets(page, info, "mood queued offline + banner");
  await context.setOffline(false);
});
