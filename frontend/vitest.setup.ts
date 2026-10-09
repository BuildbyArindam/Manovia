import "@testing-library/jest-dom/vitest";
import { toHaveNoViolations } from "jest-axe";

import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";

// The matcher's type lives in src/test/jest-axe.d.ts (a declaration file, so
// `skipLibCheck` keeps vitest's own Assertion declaration from clashing with
// @testing-library/jest-dom's augmentation of it).
expect.extend(toHaveNoViolations);

// jsdom has no matchMedia; every component that asks for a media query (theme
// detection, reduced motion, desktop/mobile navigation) would otherwise throw.
// Tests that care stub `window.matchMedia` themselves.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

// localStorage is available in jsdom; clear it between tests so the onboarding
// and theme state never leaks from one test into the next.
beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
