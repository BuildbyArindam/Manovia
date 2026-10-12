import "@testing-library/jest-dom/vitest";
import { toHaveNoViolations } from "jest-axe";

import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";

import { registerLanguageResources } from "@/lib/i18n/i18n";
import bn from "@/lib/i18n/locales/bn.json";
import hi from "@/lib/i18n/locales/hi.json";

// Day 25: production loads Hindi and Bengali on demand (their own chunks). The
// tests register both up front, so switching language stays synchronous here
// exactly as it was before the split.
registerLanguageResources("hi", hi);
registerLanguageResources("bn", bn);

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

// Recharts measures its container with a ResizeObserver even when the chart is
// given explicit width and height (the case in this app: no ResponsiveContainer,
// because a container that measures zero in jsdom renders an empty chart and a
// test that passes for the wrong reason). jsdom has no ResizeObserver.
if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}
