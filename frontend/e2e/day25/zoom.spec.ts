/**
 * Day 25 — zoom and text resize, measured in a real browser.
 *
 * Browser zoom on a 1280x800 window is a smaller CSS viewport at a higher
 * device pixel ratio: 200% is 640x400 @2x and 400% is 320x200 @4x (WCAG
 * 1.4.10 reflow is defined at exactly that 320px width). Text-only resize
 * (WCAG 1.4.4) is a larger default font size — the app's root size is
 * `max(100%, 16px)`, so a reader who sets 32px gets 32px.
 *
 * For every route, each mode checks:
 *
 * 1. **No horizontal scrolling of the page** (wide data tables scroll inside
 *    their own focusable region, which 1.4.10 allows).
 * 2. **No clipped text**: no element with hidden overflow is cutting off its
 *    own text.
 * 3. **Chrome that gets out of the way**: on short viewports the header and
 *    bottom navigation stop being sticky/fixed.
 * 4. **Focus never hidden**: the first 30 Tab stops are each checked with
 *    `elementFromPoint` — the focused control must be the topmost thing at its
 *    own centre (WCAG 2.4.11).
 *
 * Findings go to `test-results/day25/zoom-<mode>.json`; the test fails on any.
 */

import { mkdirSync, writeFileSync } from "node:fs";

import { expect, test, type Browser, type Page } from "@playwright/test";

import { APP_ROUTES, apiReachable, completeGuestOnboarding, openRoute } from "./helpers";
import { REPORT_DIR } from "./reports";

const MODES = [
  { name: "zoom 200%", viewport: { width: 640, height: 400 }, scale: 2, fontPx: null },
  { name: "zoom 400%", viewport: { width: 320, height: 200 }, scale: 4, fontPx: null },
  { name: "text 200%", viewport: { width: 1280, height: 800 }, scale: 1, fontPx: 32 },
] as const;

interface Finding {
  mode: string;
  route: string;
  kind: "horizontal-overflow" | "clipped-text" | "sticky-chrome" | "focus-obscured";
  detail: string;
}

const findings: Finding[] = [];
const measured: Array<{ mode: string; route: string; scrollWidth: number; innerWidth: number }> =
  [];

async function inspect(page: Page, mode: string, route: string): Promise<void> {
  const layout = await page.evaluate(() => {
    const doc = document.documentElement;
    const clipped: string[] = [];
    for (const element of Array.from(document.body.querySelectorAll<HTMLElement>("*"))) {
      const style = window.getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden") continue;
      const hides =
        ["hidden", "clip"].includes(style.overflowX) || style.textOverflow === "ellipsis";
      if (!hides) continue;
      if (element.closest(".sr-only, [aria-hidden=true]")) continue;
      if (element.classList.contains("sr-only")) continue;
      const text = (element.innerText ?? "").trim();
      if (text === "") continue;
      if (element.scrollWidth > element.clientWidth + 1) {
        clipped.push(
          `${element.tagName.toLowerCase()}.${element.className.toString().split(" ").slice(0, 3).join(".")} "${text.slice(0, 40)}"`,
        );
      }
    }
    const header = document.querySelector("header");
    const bottomNav = document.querySelector(".app-bottom-nav");
    return {
      scrollWidth: doc.scrollWidth,
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      clipped,
      headerPosition: header === null ? null : window.getComputedStyle(header).position,
      navPosition: bottomNav === null ? null : window.getComputedStyle(bottomNav).position,
    };
  });
  measured.push({ mode, route, scrollWidth: layout.scrollWidth, innerWidth: layout.innerWidth });
  if (layout.scrollWidth > layout.innerWidth + 1) {
    findings.push({
      mode,
      route,
      kind: "horizontal-overflow",
      detail: `scrollWidth ${layout.scrollWidth} > ${layout.innerWidth}`,
    });
  }
  for (const item of layout.clipped) {
    findings.push({ mode, route, kind: "clipped-text", detail: item });
  }
  if (layout.innerHeight <= 512) {
    for (const [label, position] of [
      ["header", layout.headerPosition],
      ["bottom navigation", layout.navPosition],
    ] as const) {
      if (position !== null && position !== "static" && position !== "relative") {
        findings.push({
          mode,
          route,
          kind: "sticky-chrome",
          detail: `${label} is ${position} at ${layout.innerHeight}px tall`,
        });
      }
    }
  }

  // Focus not obscured: walk the first 30 tab stops.
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    window.scrollTo(0, 0);
  });
  for (let i = 0; i < 30; i += 1) {
    await page.keyboard.press("Tab");
    // Focus scrolling is animated (scroll-behavior: smooth) and the skip link
    // slides in: measure where things come to rest, not mid-animation.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          let last = "";
          let stable = 0;
          const started = performance.now();
          const tick = (): void => {
            const element = document.activeElement as HTMLElement | null;
            const box = element?.getBoundingClientRect();
            const now =
              box === undefined
                ? ""
                : `${Math.round(box.top)}:${Math.round(box.left)}:${window.scrollY}`;
            stable = now === last ? stable + 1 : 0;
            last = now;
            if (stable >= 3 || performance.now() - started > 1500) {
              resolve();
            } else {
              requestAnimationFrame(tick);
            }
          };
          requestAnimationFrame(tick);
        }),
    );
    const obscured = await page.evaluate(() => {
      const element = document.activeElement as HTMLElement | null;
      if (element === null || element === document.body) return null;
      const box = element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) return null;
      const x = Math.min(Math.max(box.left + box.width / 2, 0), window.innerWidth - 1);
      const y = Math.min(Math.max(box.top + box.height / 2, 0), window.innerHeight - 1);
      const top = document.elementFromPoint(x, y);
      if (top === null || element.contains(top) || top.contains(element)) return null;
      // A visually hidden radio is drawn by its label; the label on top is fine.
      const label =
        element.closest("label") ??
        (element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null);
      if (label !== null && (label.contains(top) || top.contains(label))) return null;
      return `${element.tagName.toLowerCase()} "${(element.innerText || element.getAttribute("aria-label") || "").trim().slice(0, 30)}" under ${top.tagName.toLowerCase()}.${top.className.toString().split(" ").slice(0, 2).join(".")}`;
    });
    if (obscured !== null) {
      findings.push({ mode, route, kind: "focus-obscured", detail: obscured });
    }
  }
}

async function contextFor(browser: Browser, mode: (typeof MODES)[number]) {
  const context = await browser.newContext({
    viewport: mode.viewport,
    deviceScaleFactor: mode.scale,
    baseURL: test.info().project.use.baseURL,
  });
  if (mode.fontPx !== null) {
    // The reader's browser default font size, which `max(100%, 16px)` honours.
    await context.addInitScript((px) => {
      document.addEventListener("DOMContentLoaded", () => {
        const style = document.createElement("style");
        style.textContent = `html { font-size: ${px}px !important; }`;
        document.head.append(style);
      });
    }, mode.fontPx);
  }
  return context;
}

test.beforeEach(async ({ page }, info) => {
  test.skip(info.project.name !== "desktop-1280x800", "zoom is derived from the 1280x800 window");
  test.skip(!(await apiReachable(page)), "the API is not reachable at /api/v1/ready");
});

for (const mode of MODES) {
  test(`every route at ${mode.name}`, async ({ browser }) => {
    const context = await contextFor(browser, mode);
    const page = await context.newPage();
    await completeGuestOnboarding(page);
    for (const route of APP_ROUTES) {
      await openRoute(page, route.path, route.ready);
      await inspect(page, mode.name, route.name);
    }
    await context.close();
    const mine = findings.filter((finding) => finding.mode === mode.name);
    // One file per mode: a failing test restarts the worker, so module state
    // cannot carry a whole run to an afterAll hook.
    mkdirSync(REPORT_DIR, { recursive: true });
    writeFileSync(
      `${REPORT_DIR}/zoom-${mode.name.replace(/[^a-z0-9]+/gi, "-")}.json`,
      JSON.stringify(
        { mode: mode.name, findings: mine, measured: measured.filter((m) => m.mode === mode.name) },
        null,
        2,
      ),
    );
    expect(mine.map((finding) => `${finding.route}: ${finding.kind} — ${finding.detail}`)).toEqual(
      [],
    );
  });
}
