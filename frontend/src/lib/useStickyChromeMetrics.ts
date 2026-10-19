import { useEffect } from "react";

/**
 * Keep `scroll-padding` in step with the height of the sticky chrome.
 *
 * The header and the bottom navigation are sticky/fixed, and both are laid out
 * in `rem`: at a 32 px root font (a reader's 200 % text size) the header wraps
 * to two or three rows and is well over the old fixed `scroll-padding-top` of
 * 5.5rem. Focus scrolling then left a focused control underneath the header —
 * the Day-25 focus walk caught it as "assessments: focus-obscured — button
 * 'Start the GAD-7' under div.mx-auto.flex".
 *
 * Guessing a bigger constant cannot work: the height depends on the text size,
 * the language, and the viewport. So it is measured. The CSS keeps its old
 * values as fallbacks for the first frame and for anything that never runs
 * this hook. (WCAG 2.4.11 focus not obscured.)
 */

/** CSS custom property holding the header's current height, in px. */
export const HEADER_HEIGHT_VAR = "--app-header-height";

/** CSS custom property holding the bottom navigation's current height, in px. */
export const BOTTOM_NAV_HEIGHT_VAR = "--app-bottom-nav-height";

const HEADER_SELECTOR = ".app-header";
const BOTTOM_NAV_SELECTOR = ".app-bottom-nav";

function heightOf(element: HTMLElement | null): number | null {
  if (element === null || !element.isConnected) {
    return null;
  }
  const { height } = element.getBoundingClientRect();
  if (!Number.isFinite(height) || height <= 0) {
    return null;
  }
  return Math.ceil(height);
}

/**
 * Write the chrome heights onto `root` as the custom properties the stylesheet
 * reads. Absent chrome (the bottom navigation is not rendered on desktop or in
 * overwhelmed mode) removes the property, so the CSS fallback applies rather
 * than a stale height from a previous layout.
 */
export function syncStickyChromeHeights(root: HTMLElement, doc: Document = document): void {
  const targets: ReadonlyArray<[string, string]> = [
    [HEADER_HEIGHT_VAR, HEADER_SELECTOR],
    [BOTTOM_NAV_HEIGHT_VAR, BOTTOM_NAV_SELECTOR],
  ];
  for (const [property, selector] of targets) {
    const height = heightOf(doc.querySelector<HTMLElement>(selector));
    if (height === null) {
      root.style.removeProperty(property);
    } else {
      root.style.setProperty(property, `${height}px`);
    }
  }
}

/**
 * Watch the chrome and re-measure whenever either piece changes size — a new
 * language, a text-size setting, a wrapped row, the bottom bar appearing after
 * a media query flips. Returns its own teardown.
 */
export function watchStickyChrome(doc: Document = document): () => void {
  const root = doc.documentElement;
  const update = (): void => syncStickyChromeHeights(root, doc);
  const elements = [HEADER_SELECTOR, BOTTOM_NAV_SELECTOR]
    .map((selector) => doc.querySelector<HTMLElement>(selector))
    .filter((element): element is HTMLElement => element !== null);

  // The window listeners are always attached: they are the only signal in
  // jsdom and in browsers without ResizeObserver, and they also catch an
  // orientation change that reflows the chrome without resizing either
  // observed element's box.
  window.addEventListener("resize", update);
  window.addEventListener("orientationchange", update);

  let observer: ResizeObserver | null = null;
  if (typeof ResizeObserver === "function") {
    observer = new ResizeObserver(update);
    for (const element of elements) {
      observer.observe(element);
    }
  }

  update();

  return () => {
    observer?.disconnect();
    window.removeEventListener("resize", update);
    window.removeEventListener("orientationchange", update);
    root.style.removeProperty(HEADER_HEIGHT_VAR);
    root.style.removeProperty(BOTTOM_NAV_HEIGHT_VAR);
  };
}

/**
 * Measure the sticky chrome after mount and keep it measured.
 *
 * `bottomNavVisible` is a dependency because the bar is rendered conditionally:
 * when it appears or disappears the observer has to be pointed at the new DOM.
 */
export function useStickyChromeMetrics(bottomNavVisible: boolean): void {
  useEffect(() => watchStickyChrome(), [bottomNavVisible]);
}
