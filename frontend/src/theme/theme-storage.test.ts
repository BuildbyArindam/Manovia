/**
 * The palette key is shared state: `ThemeProvider` writes it, `first-paint`
 * reads it before React mounts.
 *
 * The bug these tests exist for is not a crash — it is silence. Two copies of
 * the same string, each covered by its own tests, and a stored preference that
 * quietly stops being honoured on reload. So the tests go through the *two
 * different modules* rather than through one exported constant: whatever
 * `ThemeProvider` writes, `applyFirstPaintTheme` must read back. If someone
 * ever reintroduces a second key, this fails.
 */

import { describe, expect, it, vi } from "vitest";

import { applyFirstPaintTheme } from "./first-paint";
import { THEME_STORAGE_KEY as FIRST_PAINT_KEY } from "./first-paint";
import { THEME_STORAGE_KEY as PROVIDER_KEY } from "./ThemeProvider";
import { THEME_STORAGE_KEY } from "./theme-storage";

describe("theme storage key", () => {
  it("is one key, not two", () => {
    expect(FIRST_PAINT_KEY).toBe(PROVIDER_KEY);
    expect(THEME_STORAGE_KEY).toBe(PROVIDER_KEY);
  });

  it("is the literal the shipped bundles already use", () => {
    // Changing this orphans the stored preference of every existing reader:
    // they silently fall back to the system palette. If it ever has to move,
    // migrate the old key in the same change — do not just edit this line.
    expect(THEME_STORAGE_KEY).toBe("manovia.theme");
  });

  it("reads back what the provider writes", () => {
    // Simulate the provider's write path with the provider's own key…
    const store = new Map<string, string>([[PROVIDER_KEY, "dark"]]);
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
      removeItem: (key: string) => store.delete(key),
    });
    // …and the operating system disagreeing, so the stored choice is the only
    // thing that can produce "dark".
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    );

    expect(applyFirstPaintTheme()).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});
