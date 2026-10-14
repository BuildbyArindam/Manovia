/**
 * Day 25 — a keyboard-only walkthrough: onboarding → chat → mood → exercise →
 * settings, with no mouse anywhere (only `keyboard.press` / `keyboard.type`).
 *
 * On every surface it also *audits* the tab order:
 *
 * * **Focus traps.** Tab is pressed until focus wraps back to where it
 *   started. A walk that never wraps — focus cycling inside part of the page —
 *   is a trap. (The help dialog's cycle is the one intended trap, and it is
 *   checked separately: Escape must release it and return focus to the button
 *   that opened it.)
 * * **Unreachable controls.** Every visible, enabled interactive element that
 *   was never focused during the walk is listed. A radio group counts as
 *   reached when any of its radios was (arrow keys move inside a group).
 *
 * The findings are written to `test-results/day25/keyboard-<project>.json`
 * and the test fails on any trap or unreachable control.
 */

import { mkdirSync, writeFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

import { apiReachable } from "./helpers";
import { REPORT_DIR } from "./reports";

interface Focused {
  key: string;
  name: string;
}

interface SurfaceReport {
  surface: string;
  presses: number;
  wrapped: boolean;
  reached: number;
  unreachable: string[];
  /** The focus order actually observed (names), for the report. */
  order: string[];
}

const reports: SurfaceReport[] = [];

/** Describe (and mark) whatever has focus now. */
async function focused(page: Page): Promise<Focused> {
  return page.evaluate(() => {
    const element = document.activeElement as HTMLElement | null;
    if (element === null || element === document.body || element === document.documentElement) {
      return { key: "body", name: "" };
    }
    element.setAttribute("data-kbd-reached", "1");
    const labelledBy = element.getAttribute("aria-labelledby");
    const byLabel =
      element.id !== "" ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null;
    const name =
      element.getAttribute("aria-label") ??
      (labelledBy !== null ? document.getElementById(labelledBy)?.textContent : null) ??
      byLabel?.textContent ??
      element.closest("label")?.textContent ??
      (element as HTMLInputElement).placeholder ??
      // innerText keeps the line break between a nav label and its hint
      // ("Chat\nTalk it through"); textContent would glue them together.
      element.innerText ??
      "";
    const clean = name.trim().replace(/\s+/g, " ").slice(0, 80);
    // A stable identity for this element across re-renders (a DOM index would
    // shift whenever the page adds a node, e.g. a streamed chat reply).
    const store = window as unknown as { __kbdNext?: number };
    if (!element.dataset.kbdId) {
      store.__kbdNext = (store.__kbdNext ?? 0) + 1;
      element.dataset.kbdId = String(store.__kbdNext);
    }
    return { key: `${element.tagName.toLowerCase()}#${element.dataset.kbdId}`, name: clean };
  });
}

/** Press Tab until focus is on something whose name matches; fail if it never is. */
async function tabTo(page: Page, matcher: RegExp, max = 80): Promise<void> {
  for (let i = 0; i < max; i += 1) {
    await page.keyboard.press("Tab");
    const now = await focused(page);
    if (matcher.test(now.name)) {
      return;
    }
  }
  throw new Error(`keyboard: could not reach a control matching ${matcher} within ${max} Tabs`);
}

/** Move focus back to the top of the document without touching the mouse. */
async function resetFocus(page: Page): Promise<void> {
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    document.querySelectorAll("[data-kbd-reached]").forEach((element) => {
      element.removeAttribute("data-kbd-reached");
    });
    // Instant: under `scroll-behavior: smooth` a plain scrollTo animates and
    // races the focus scrolling that the next Tab triggers.
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  });
}

/**
 * Hooks the regression test uses to reproduce a tab-walk race deterministically.
 *
 * The chat's "Jump to latest" button is the reason these exist. It is rendered
 * the moment the conversation un-pins — which the browser can cause by revealing
 * the focused node inside the scroll container — and that `scroll` event lands
 * asynchronously, so the button can appear *after* the walk's last Tab and
 * *before* the unreached-controls query. A control that was never given the
 * chance to be tabbed to is then reported as unreachable, which is a lie, and
 * which is what failed this suite on CI once in every few runs.
 */
interface AuditHooks {
  /** Runs after the walk's last Tab and before the unreached controls are read. */
  afterWalk?: () => Promise<void>;
}

/** Walk the whole tab order once; report wrapping and unreached controls. */
async function audit(page: Page, surface: string, hooks: AuditHooks = {}): Promise<SurfaceReport> {
  await resetFocus(page);
  const total = await page.evaluate(
    () =>
      document.querySelectorAll(
        "a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex='-1'])",
      ).length,
  );
  const max = total * 2 + 20;
  const walkOnce = async (): Promise<{ sequence: Focused[]; wrapped: boolean }> => {
    const sequence: Focused[] = [];
    let wrapped = false;
    for (let i = 0; i < max; i += 1) {
      await page.keyboard.press("Tab");
      const now = await focused(page);
      sequence.push(now);
      // Wrapped: the first element of the walk has come round again.
      const first = sequence.find((item) => item.key !== "body");
      if (first !== undefined && sequence.filter((item) => item.key === first.key).length > 1) {
        wrapped = true;
        break;
      }
    }
    return { sequence, wrapped };
  };
  const unreached = (): Promise<string[]> =>
    page.evaluate(() => {
    const selector =
      "a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], [role=checkbox], [role=switch], [role=radio], [tabindex]:not([tabindex='-1'])";
    const reachedGroups = new Set(
      Array.from(
        document.querySelectorAll<HTMLInputElement>("input[type=radio][data-kbd-reached]"),
      ).map((radio) => radio.name),
    );
    const out: string[] = [];
    for (const element of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      if (element.hasAttribute("data-kbd-reached")) continue;
      if ((element as HTMLButtonElement).disabled) continue;
      // tabindex=-1 is a legitimate *focus target* on a heading or <main>, but on
      // a native control it removes the control from the keyboard: report it.
      const native = ["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY"].includes(
        element.tagName,
      );
      if (element.getAttribute("tabindex") === "-1" && !native) continue;
      if (element.closest("[inert], [aria-hidden=true]")) continue;
      const style = window.getComputedStyle(element);
      const box = element.getBoundingClientRect();
      const visuallyHiddenRadio =
        element instanceof HTMLInputElement &&
        (element.type === "radio" || element.type === "checkbox");
      if (
        !visuallyHiddenRadio &&
        (style.visibility === "hidden" ||
          style.display === "none" ||
          (box.width === 0 && box.height === 0))
      )
        continue;
      if (element.closest("details:not([open])") && element.tagName !== "SUMMARY") continue;
      if (
        element instanceof HTMLInputElement &&
        element.type === "radio" &&
        reachedGroups.has(element.name)
      )
        continue;
      out.push(
        `${element.tagName.toLowerCase()} ${element.getAttribute("aria-label") ?? element.textContent?.trim().slice(0, 50) ?? ""} ${element.dataset.testid ?? ""}`.trim(),
      );
    }
    return out;
  });

  let walked = await walkOnce();
  if (hooks.afterWalk !== undefined) {
    await hooks.afterWalk();
  }
  let unreachable = await unreached();
  // A control that arrived while the walk was running was never offered a Tab,
  // so walk once more before calling anything unreachable. Bounded, and only on
  // the path where something was missed: a genuinely unreachable control still
  // fails, it just fails after one honest second chance.
  if (unreachable.length > 0) {
    await resetFocus(page);
    walked = await walkOnce();
    unreachable = await unreached();
  }

  const { sequence, wrapped } = walked;
  const report: SurfaceReport = {
    surface,
    presses: sequence.length,
    wrapped,
    reached: new Set(sequence.map((item) => item.key)).size,
    unreachable,
    order: sequence.map((item) => `${item.key.split("#")[0]}: ${item.name.slice(0, 40)}`),
  };
  reports.push(report);
  expect.soft(wrapped, `${surface}: Tab never wrapped around the page (focus trap?)`).toBe(true);
  expect.soft(unreachable, `${surface}: controls the keyboard never reached`).toEqual([]);
  return report;
}

test.beforeEach(async ({ page }) => {
  test.skip(!(await apiReachable(page)), "the API is not reachable at /api/v1/ready");
});

test.afterAll(() => {
  mkdirSync(REPORT_DIR, { recursive: true });
  writeFileSync(
    `${REPORT_DIR}/keyboard-${test.info().project.name}.json`,
    JSON.stringify(reports, null, 2),
  );
});

test("onboarding → chat → mood → exercise → settings, keyboard only", async ({ page }) => {
  // ---- onboarding --------------------------------------------------------
  await page.goto("/");
  await audit(page, "onboarding step 1");
  for (let step = 0; step < 3; step += 1) {
    await resetFocus(page);
    await tabTo(page, /^Continue$/);
    await page.keyboard.press("Enter");
  }
  await resetFocus(page);
  for (const agreement of [/terms/i, /privacy/i, /AI|artificial/i]) {
    await tabTo(page, agreement);
    await page.keyboard.press(" ");
  }
  await expect(page.locator("#consent-terms")).toBeChecked();
  await expect(page.locator("#consent-privacy")).toBeChecked();
  await expect(page.locator("#consent-ai_disclosure")).toBeChecked();
  await tabTo(page, /^Continue$/);
  await page.keyboard.press("Enter");
  await audit(page, "onboarding step 5");
  await resetFocus(page);
  await tabTo(page, /^Continue as a guest$/);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("home-page")).toBeVisible();
  await audit(page, "home");

  // ---- the help dialog: the one intended trap ----------------------------
  await resetFocus(page);
  await tabTo(page, /^Need help now\?$/);
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('a[href^="tel:"]').first()).toBeVisible();
  for (let i = 0; i < 25; i += 1) {
    await page.keyboard.press("Tab");
    expect(
      await page.evaluate(
        () => document.querySelector("[role=dialog]")?.contains(document.activeElement) ?? false,
      ),
      "focus stays inside the open dialog",
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect((await focused(page)).name).toBe("Need help now?");

  // ---- chat --------------------------------------------------------------
  await resetFocus(page);
  await tabTo(page, /^Chat\b/);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/chat$/);
  await tabTo(page, /what is on your mind/i);
  await page.keyboard.type("I had a long day and want to slow down.");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("assistant-text").last()).not.toBeEmpty({ timeout: 30_000 });
  await audit(page, "chat (after a reply)");

  // ---- mood --------------------------------------------------------------
  await resetFocus(page);
  await tabTo(page, /^Mood\b/);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("mood-page")).toBeVisible();
  await tabTo(page, /^Very low$/);
  await page.keyboard.press(" ");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("radio", { name: "In the middle", exact: true })).toBeChecked();
  await tabTo(page, /^Save check-in$/);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Saved." })).toBeFocused();
  await audit(page, "mood (confirmation)");

  // ---- exercise ----------------------------------------------------------
  await resetFocus(page);
  await tabTo(page, /^Exercises\b/);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("exercises-page")).toBeVisible();
  await tabTo(page, /^Try Box breathing$/);
  await page.keyboard.press("Enter");
  await tabTo(page, /^Start exercise$/);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("timer")).toBeVisible();
  await tabTo(page, /^Pause$/);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Resume" })).toBeFocused();
  await audit(page, "exercise runner (paused)");
  await resetFocus(page);
  await tabTo(page, /^Stop exercise$/);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Try Box breathing" })).toBeFocused();

  // ---- settings ----------------------------------------------------------
  await resetFocus(page);
  await tabTo(page, /^Settings\b/);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("install-section")).toBeVisible();
  // Into the contrast group (focus lands on the checked radio), then arrows.
  await tabTo(page, /Match my device.*system contrast/i);
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator("html")).toHaveAttribute("data-contrast", "more");
  await audit(page, "settings (high contrast on)");
});

/**
 * The race the `audit` second pass exists for, reproduced on purpose.
 *
 * In the real app the chat's "Jump to latest" button arrives between the walk's
 * last Tab and the unreached-controls query, because the `scroll` event that
 * un-pins the conversation is asynchronous. That is a timing accident, so this
 * test makes it happen on demand: append a control at exactly that moment and
 * hold the audit to the property it claims to check — that every keyboard-
 * reachable control is reachable by Tab, including one that showed up late.
 *
 * Before the second pass this fails with the control named in `unreachable`;
 * after it, the walk is repeated and the control is reached.
 */
test("a control that arrives mid-walk is still held to the tab order", async ({ page }) => {
  await page.goto("/");
  await audit(page, "home, with a control that arrives late", {
    afterWalk: async () => {
      await page.evaluate(() => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "A control that arrived late";
        button.dataset.testid = "late-arriving-control";
        document.body.append(button);
      });
    },
  });
});
