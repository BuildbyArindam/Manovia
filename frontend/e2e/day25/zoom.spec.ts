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
    // Instant: under `scroll-behavior: smooth` a plain scrollTo animates and
    // races the focus scrolling that the next Tab triggers.
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
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
      const name = `${element.tagName.toLowerCase()} "${(element.innerText || element.getAttribute("aria-label") || "").trim().slice(0, 30)}"`;
      // Entirely off screen is the worst case of "obscured": say so, rather
      // than naming whatever happens to sit at the clamped probe point.
      if (
        box.bottom <= 0 ||
        box.top >= window.innerHeight ||
        box.right <= 0 ||
        box.left >= window.innerWidth
      ) {
        return `${name} is outside the viewport (top ${Math.round(box.top)}px of ${window.innerHeight}px)`;
      }
      const x = Math.min(Math.max(box.left + box.width / 2, 0), window.innerWidth - 1);
      const y = Math.min(Math.max(box.top + box.height / 2, 0), window.innerHeight - 1);
      const top = document.elementFromPoint(x, y);
      if (top === null || element.contains(top) || top.contains(element)) return null;
      // A visually hidden radio is drawn by its label; the label on top is fine.
      const label =
        element.closest("label") ??
        (element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null);
      if (label !== null && (label.contains(top) || top.contains(label))) return null;
      return `${name} under ${top.tagName.toLowerCase()}.${top.className.toString().split(" ").slice(0, 2).join(".")}`;
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

/**
 * Regression (Day 25 verification). On a short screen the bottom navigation
 * scrolls with the page, and revealing its current item with
 * `scrollIntoView` dragged every freshly opened page down to the bar: the
 * reader landed at the end of the page, and their first Tab went to the bar
 * ("Chat") instead of "Skip to main content". A page must open at its top,
 * Tab must start at the skip link, and the current item must still be visible
 * inside the bar (Settings is the last of nine, off the edge at 640px).
 */
test("on a short screen a page opens at its top and Tab starts at the skip link", async ({
  browser,
}) => {
  for (const mode of MODES.filter((candidate) => candidate.fontPx === null)) {
    const context = await contextFor(browser, mode);
    const page = await context.newPage();
    await completeGuestOnboarding(page);
    for (const route of APP_ROUTES.filter((candidate) =>
      ["home", "exercises", "settings"].includes(candidate.name),
    )) {
      await openRoute(page, route.path, route.ready);
      const state = await page.evaluate(() => {
        const nav = document.querySelector<HTMLElement>(".app-bottom-nav");
        const list = nav?.querySelector("ul")?.getBoundingClientRect();
        const item = nav
          ?.querySelector('[aria-current="page"]')
          ?.closest("li")
          ?.getBoundingClientRect();
        return {
          scrollY: Math.round(window.scrollY),
          navPosition: nav === null || nav === undefined ? null : getComputedStyle(nav).position,
          currentItemVisible:
            list === undefined || item === undefined
              ? null
              : item.left >= list.left - 1 && item.right <= list.right + 1,
        };
      });
      const where = `${mode.name} ${route.path}`;
      // The scenario only exists while the bar scrolls with the page.
      expect(state.navPosition, `${where}: bottom navigation`).toBe("static");
      expect(state.scrollY, `${where}: opened scrolled down`).toBe(0);
      expect(state.currentItemVisible, `${where}: current item visible in the bar`).toBe(true);
      await page.keyboard.press("Tab");
      await expect(page.locator(":focus"), `${where}: first Tab stop`).toHaveText(
        "Skip to main content",
      );
    }
    await context.close();
  }
});

/**
 * Regression (Day 25 verification). A select is as wide as its longest
 * option. With the pseudo-localisation option present (every dev build, or
 * `?pseudo=1` for QA), the language select grew to ~370px, pushing the header
 * 73px past a 360px screen in plain English and squeezing "Need help now?" to
 * 69px (the Day 18 exercises spec caught it on the dev server). The select is
 * now bounded at 7rem.
 *
 * English, Hindi and Bengali — which no browser had checked at 320px before —
 * must fit whole: page, header row, and a whole on-screen help button. Pseudo
 * mode is checked for the root cause only (the select stays within 7rem): its
 * padded strings expand even the brand name (83px → 144px), so its header and
 * many labels still overflow at 360px — a known pseudo-mode gap recorded in
 * PROGRESS.md.
 */
test("the header fits 360px and 320px screens in every language; the language select stays bounded", async ({
  browser,
}) => {
  for (const width of [360, 320]) {
    const context = await browser.newContext({
      viewport: { width, height: 640 },
      baseURL: test.info().project.use.baseURL,
    });
    const page = await context.newPage();
    await completeGuestOnboarding(page);
    const measure = () =>
      page.evaluate(() => {
        const row = document.querySelector<HTMLElement>("header > div");
        // "Need help now?" in any language: the header's only dialog button.
        const help = document
          .querySelector<HTMLElement>("header button[aria-haspopup='dialog']")
          ?.getBoundingClientRect();
        const select = document
          .querySelector<HTMLElement>("[data-testid='language-switcher']")
          ?.getBoundingClientRect();
        const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
        return {
          scrollWidth: document.documentElement.scrollWidth,
          innerWidth: window.innerWidth,
          headerOverflow: row === null ? null : row.scrollWidth - row.clientWidth,
          help:
            help === undefined
              ? null
              : {
                  width: Math.round(help.width),
                  height: Math.round(help.height),
                  inside: help.left >= 0 && help.right <= window.innerWidth,
                },
          selectWithinBound: select === undefined ? null : select.width <= 7 * rem + 0.5,
        };
      });

    for (const language of ["en", "hi", "bn"] as const) {
      await page.getByTestId("language-switcher").selectOption(language);
      await expect(page.locator("html")).toHaveAttribute("lang", language);
      await expect(page.getByTestId("home-page")).toBeVisible();
      const layout = await measure();
      const where = `${width}px ${language}`;
      expect(layout.scrollWidth, `${where}: page scrolls sideways`).toBeLessThanOrEqual(
        layout.innerWidth,
      );
      expect(layout.headerOverflow, `${where}: header row overflows by`).toBe(0);
      expect(layout.help?.inside, `${where}: help button on screen`).toBe(true);
      expect(layout.help?.width ?? 0, `${where}: help button width`).toBeGreaterThanOrEqual(44);
      expect(layout.help?.height ?? 0, `${where}: help button height`).toBeGreaterThanOrEqual(44);
      expect(layout.selectWithinBound, `${where}: language select within 7rem`).toBe(true);
    }

    await page.goto("/?pseudo=1");
    await expect(page.getByTestId("language-switcher")).toHaveValue("pseudo");
    const pseudo = await measure();
    expect(pseudo.selectWithinBound, `${width}px pseudo: language select within 7rem`).toBe(true);
    expect(
      pseudo.help?.height ?? 0,
      `${width}px pseudo: help button height`,
    ).toBeGreaterThanOrEqual(44);
    await page.goto("/?pseudo=0");
    await context.close();
  }
});

/**
 * Regression (Day 46 CI failure). The header gained a third control ("I'm
 * overwhelmed"). At 400% zoom the row no longer fits, and because the brand
 * could shrink to zero width while its link kept its own min-content width, the
 * link's box was laid out on top of the controls beside it. The Day-25 focus
 * walk caught exactly that: "a 'Manovia' under span.sm:hidden".
 *
 * The row may wrap now, and nothing in it may shrink below its content. This
 * test is the general form of the finding: no two header controls may overlap,
 * and every control's own centre must be its own topmost pixel — at the two
 * widths the header is designed for, on the first two routes.
 */
test("header controls never overlap or cover each other, at 360px and at 400% zoom", async ({
  browser,
}) => {
  for (const screen of [
    { width: 360, height: 640, scale: 1, label: "360x640" },
    { width: 320, height: 200, scale: 4, label: "400% zoom (320x200 @4x)" },
  ]) {
    const context = await browser.newContext({
      viewport: { width: screen.width, height: screen.height },
      deviceScaleFactor: screen.scale,
      baseURL: test.info().project.use.baseURL,
    });
    const page = await context.newPage();
    await completeGuestOnboarding(page);

    for (const path of ["/", "/chat"] as const) {
      await openRoute(page, path, path === "/" ? "home-page" : null);
      const problems = await page.evaluate(() => {
        const found: string[] = [];
        const controls = Array.from(
          document.querySelectorAll<HTMLElement>("header a, header button, header select"),
        ).filter((element) => element.getClientRects().length > 0);
        const describe = (element: HTMLElement): string =>
          `${element.tagName.toLowerCase()} "${(element.innerText || element.getAttribute("aria-label") || "").trim().slice(0, 24)}"`;
        const boxes = controls.map((element) => ({
          element,
          name: describe(element),
          box: element.getBoundingClientRect(),
        }));

        for (let i = 0; i < boxes.length; i += 1) {
          for (let j = i + 1; j < boxes.length; j += 1) {
            const a = boxes[i]!;
            const b = boxes[j]!;
            // A 1px tolerance: adjacent borders may share an edge.
            const overlap =
              a.box.left < b.box.right - 1 &&
              b.box.left < a.box.right - 1 &&
              a.box.top < b.box.bottom - 1 &&
              b.box.top < a.box.bottom - 1;
            if (overlap) {
              found.push(`${a.name} overlaps ${b.name}`);
            }
          }
        }

        for (const { element, name, box } of boxes) {
          // Off screen entirely is its own failure, reported plainly.
          if (box.right <= 0 || box.left >= window.innerWidth) {
            found.push(`${name} is off screen`);
            continue;
          }
          const x = Math.min(Math.max(box.left + box.width / 2, 0), window.innerWidth - 1);
          const y = Math.min(Math.max(box.top + box.height / 2, 0), window.innerHeight - 1);
          const top = document.elementFromPoint(x, y);
          if (top === null) {
            found.push(`${name} has nothing at its centre`);
            continue;
          }
          if (!element.contains(top) && !top.contains(element)) {
            found.push(
              `${name} is covered by ${top.tagName.toLowerCase()}.${top.className.toString().split(" ").slice(0, 2).join(".")}`,
            );
          }
        }
        return found;
      });
      expect(problems, `${screen.label} ${path}`).toEqual([]);
      if (path === "/") {
        // The regression's own symptom, stated as the rule: the brand is the
        // topmost thing at its centre, and "Need help now?" is still reachable.
        await expect(page.getByRole("link", { name: "Manovia" })).toBeVisible();
        await expect(page.getByRole("button", { name: /Need help now/i })).toBeVisible();
      }
    }
    await context.close();
  }
});
