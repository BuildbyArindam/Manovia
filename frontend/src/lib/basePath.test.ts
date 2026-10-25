import { describe, expect, it } from "vitest";

import { appShellUrl, routerBasename } from "@/lib/basePath";

/**
 * These two values decide whether the app works when it is *not* served from
 * the origin root — GitHub Pages project sites live under `/Manovia/`, and both
 * mistakes they guard against fail silently (a not-found page, or a service
 * worker that throws at install) rather than loudly.
 */
describe("routerBasename", () => {
  it("keeps a root deployment at the root", () => {
    expect(routerBasename("/")).toBe("/");
  });

  it("strips the trailing slash Vite always adds", () => {
    expect(routerBasename("/Manovia/")).toBe("/Manovia");
  });

  it("handles a nested subpath", () => {
    expect(routerBasename("/apps/manovia/")).toBe("/apps/manovia");
  });

  it("accepts a base without a trailing slash or a leading slash", () => {
    expect(routerBasename("/Manovia")).toBe("/Manovia");
    expect(routerBasename("Manovia/")).toBe("/Manovia");
  });

  it("reduces an absolute base to its path", () => {
    expect(routerBasename("https://cdn.example.com/app/")).toBe("/app");
    expect(routerBasename("https://example.com/")).toBe("/");
  });

  it("treats an empty base as the root", () => {
    expect(routerBasename("")).toBe("/");
    expect(routerBasename("   ")).toBe("/");
  });
});

describe("appShellUrl", () => {
  it("resolves the shell next to the worker for a project site", () => {
    expect(appShellUrl("https://buildbyarindam.github.io/Manovia/sw.js")).toBe(
      "https://buildbyarindam.github.io/Manovia/index.html",
    );
  });

  it("resolves the shell at the origin root", () => {
    expect(appShellUrl("https://manovia.example.com/sw.js")).toBe(
      "https://manovia.example.com/index.html",
    );
  });

  it("accepts a location object, as `self.location` in the worker is", () => {
    expect(appShellUrl({ href: "https://example.com/app/sw.js" })).toBe(
      "https://example.com/app/index.html",
    );
  });
});
