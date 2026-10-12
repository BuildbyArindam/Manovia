/**
 * The palette must be right on the first frame, and getting it right must not
 * need an inline script (which a strict CSP forbids). These tests drive the
 * module directly, plus the `ThemeProvider` it mirrors, so the two cannot
 * disagree about what "system" means.
 */

import { describe, expect, it, vi } from "vitest";

import { THEME_STORAGE_KEY } from "./first-paint";

/** Load the module fresh with a stubbed `window`, as a page load would. */
async function runWith(options: { stored?: string | null; systemDark?: boolean }): Promise<string> {
  const store = new Map<string, string>();
  if (options.stored != null) {
    store.set(THEME_STORAGE_KEY, options.stored);
  }
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value),
    removeItem: (key: string) => store.delete(key),
  });
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: options.systemDark ?? false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  document.documentElement.removeAttribute("data-theme");

  vi.resetModules();
  await import("./first-paint");
  return document.documentElement.getAttribute("data-theme") ?? "missing";
}

describe("first paint theme", () => {
  it("follows the operating system when no choice was made", async () => {
    expect(await runWith({ systemDark: true })).toBe("dark");
    expect(await runWith({ systemDark: false })).toBe("light");
  });

  it("honours an explicit choice over the system", async () => {
    expect(await runWith({ stored: "light", systemDark: true })).toBe("light");
    expect(await runWith({ stored: "dark", systemDark: false })).toBe("dark");
  });

  it("ignores a stored value that is not a palette", async () => {
    expect(await runWith({ stored: "solarized", systemDark: true })).toBe("dark");
  });

  it("never throws when storage is unavailable (private mode)", async () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("storage blocked");
      },
    });
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false })),
    );
    document.documentElement.removeAttribute("data-theme");
    vi.resetModules();

    await expect(import("./first-paint")).resolves.toBeDefined();
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});
