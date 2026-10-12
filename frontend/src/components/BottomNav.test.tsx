/**
 * The bottom navigation shows the current page's item by scrolling **itself**
 * sideways, never the page (Day 25 verification).
 *
 * It used `scrollIntoView`, which also scrolls the document. On short screens
 * the bar is not fixed (it scrolls with the page), so every freshly opened
 * page jumped down to the bottom navigation and the first Tab went to the bar
 * instead of "Skip to main content". `e2e/day25/zoom.spec.ts` checks the real
 * layout in Chromium; these tests pin the mechanism.
 */

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BottomNav, revealInStrip } from "./BottomNav";

function box(left: number, width: number): DOMRect {
  return {
    left,
    right: left + width,
    width,
    top: 0,
    bottom: 48,
    height: 48,
    x: left,
    y: 0,
    toJSON: () => ({}),
  };
}

/** Just enough of an element for `revealInStrip`: a box and a sideways scroll offset. */
function fakeElement(rect: DOMRect, scrollLeft = 0): HTMLElement {
  return { getBoundingClientRect: () => rect, scrollLeft } as unknown as HTMLElement;
}

afterEach(() => {
  vi.restoreAllMocks();
  // jsdom has no scrollIntoView; remove the stub a test may have installed.
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

describe("revealInStrip", () => {
  it("scrolls right just far enough to show an item past the right edge", () => {
    const strip = fakeElement(box(0, 360), 0);
    revealInStrip(strip, fakeElement(box(600, 72)));
    expect(strip.scrollLeft).toBe(312);
  });

  it("scrolls left just far enough to show an item before the left edge", () => {
    const strip = fakeElement(box(0, 360), 400);
    revealInStrip(strip, fakeElement(box(-80, 72)));
    expect(strip.scrollLeft).toBe(320);
  });

  it("leaves the strip alone when the item is already visible", () => {
    const strip = fakeElement(box(0, 360), 50);
    revealInStrip(strip, fakeElement(box(100, 72)));
    expect(strip.scrollLeft).toBe(50);
  });

  it("never scrolls the page", () => {
    const pageScroll = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const pageScrollBy = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    revealInStrip(fakeElement(box(0, 360)), fakeElement(box(600, 72)));
    expect(pageScroll).not.toHaveBeenCalled();
    expect(pageScrollBy).not.toHaveBeenCalled();
  });
});

describe("BottomNav", () => {
  it("does not use scrollIntoView, which would also scroll the page", () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      writable: true,
      value: scrollIntoView,
    });
    const pageScroll = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);

    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <BottomNav />
      </MemoryRouter>,
    );

    // The current page is marked, so there was something to reveal.
    expect(screen.getByRole("link", { current: "page" })).toBeInTheDocument();
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(pageScroll).not.toHaveBeenCalled();
  });
});
