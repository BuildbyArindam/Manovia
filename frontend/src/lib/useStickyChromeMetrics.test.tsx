import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BOTTOM_NAV_HEIGHT_VAR,
  HEADER_HEIGHT_VAR,
  syncStickyChromeHeights,
  useStickyChromeMetrics,
  watchStickyChrome,
} from "@/lib/useStickyChromeMetrics";

/** Give an element a fixed layout height; jsdom has no layout engine. */
function stubHeight(element: HTMLElement, height: number): void {
  element.getBoundingClientRect = () =>
    ({
      x: 0,
      y: 0,
      width: 320,
      height,
      top: 0,
      right: 320,
      bottom: height,
      left: 0,
      toJSON: () => ({}),
    }) as DOMRect;
}

function chrome(html: string): void {
  document.body.innerHTML = html;
}

function header(): HTMLElement {
  const element = document.querySelector<HTMLElement>(".app-header");
  if (element === null) {
    throw new Error("header fixture missing");
  }
  return element;
}

function bottomNav(): HTMLElement {
  const element = document.querySelector<HTMLElement>(".app-bottom-nav");
  if (element === null) {
    throw new Error("bottom nav fixture missing");
  }
  return element;
}

const root = (): HTMLElement => document.documentElement;

beforeEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  root().style.removeProperty(HEADER_HEIGHT_VAR);
  root().style.removeProperty(BOTTOM_NAV_HEIGHT_VAR);
});

afterEach(() => {
  document.body.innerHTML = "";
  root().style.removeProperty(HEADER_HEIGHT_VAR);
  root().style.removeProperty(BOTTOM_NAV_HEIGHT_VAR);
});

describe("syncStickyChromeHeights", () => {
  it("writes the measured heights as custom properties, rounded up", () => {
    chrome('<header class="app-header"></header><nav class="app-bottom-nav"></nav>');
    stubHeight(header(), 88.2);
    stubHeight(bottomNav(), 63.4);

    syncStickyChromeHeights(root());

    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("89px");
    expect(root().style.getPropertyValue(BOTTOM_NAV_HEIGHT_VAR)).toBe("64px");
  });

  it("measures the header as it actually wrapped, not as a constant", () => {
    // 200% text: the header is three rows tall, far past the old 5.5rem.
    chrome('<header class="app-header"></header>');
    stubHeight(header(), 236);
    syncStickyChromeHeights(root());
    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("236px");

    // A wider viewport, one row.
    stubHeight(header(), 64);
    syncStickyChromeHeights(root());
    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("64px");
  });

  it("removes the property when that piece of chrome is not rendered", () => {
    chrome('<header class="app-header"></header>');
    stubHeight(header(), 88);
    root().style.setProperty(BOTTOM_NAV_HEIGHT_VAR, "999px");

    syncStickyChromeHeights(root());

    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("88px");
    // No bottom bar on this screen: the CSS fallback, not a stale height.
    expect(root().style.getPropertyValue(BOTTOM_NAV_HEIGHT_VAR)).toBe("");
  });

  it("ignores chrome that has no layout yet", () => {
    chrome('<header class="app-header"></header>');
    stubHeight(header(), 0);
    syncStickyChromeHeights(root());
    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("");
  });
});

describe("watchStickyChrome", () => {
  it("keeps the measurement current when the layout changes", () => {
    chrome('<header class="app-header"></header><nav class="app-bottom-nav"></nav>');
    stubHeight(header(), 88);
    stubHeight(bottomNav(), 64);

    const stop = watchStickyChrome();
    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("88px");

    stubHeight(header(), 240);
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });

    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("240px");

    stop();
    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("");
  });
});

describe("useStickyChromeMetrics", () => {
  it("measures on mount and again when the bottom bar appears", () => {
    document.body.innerHTML = '<div><header class="app-header"></header></div>';
    stubHeight(header(), 88);

    const { rerender } = renderHook(
      ({ bottomNavVisible }: { bottomNavVisible: boolean }) =>
        useStickyChromeMetrics(bottomNavVisible),
      { initialProps: { bottomNavVisible: false } },
    );
    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("88px");
    expect(root().style.getPropertyValue(BOTTOM_NAV_HEIGHT_VAR)).toBe("");

    // The mobile bar mounts; the hook has to pick it up now, and the header has
    // wrapped into a different shape at the same time.
    document.body.innerHTML =
      '<div><header class="app-header"></header><nav class="app-bottom-nav"></nav></div>';
    stubHeight(header(), 176);
    stubHeight(bottomNav(), 72);

    rerender({ bottomNavVisible: true });

    expect(root().style.getPropertyValue(HEADER_HEIGHT_VAR)).toBe("176px");
    expect(root().style.getPropertyValue(BOTTOM_NAV_HEIGHT_VAR)).toBe("72px");
  });
});
