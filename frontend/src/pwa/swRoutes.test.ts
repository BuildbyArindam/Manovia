/**
 * The service worker's privacy boundary, as tests (Day 25).
 *
 * `src/sw/sw.ts` routes requests with exactly these predicates, so what they
 * accept is what the worker may store on the device. Two public answers may be
 * cached; nothing personal ever may — including a request that *looks* public
 * but carries a session.
 */

import { describe, expect, it } from "vitest";

import { filterPrecacheManifest } from "./precache";
import {
  CACHE_NAMES,
  EXERCISES_PATH,
  HELPLINES_PATH,
  WARM_URLS,
  isAppShellNavigation,
  isCacheableStatus,
  isExercisesRequest,
  isHelplinesRequest,
  isPrivateAssetRequest,
  type RequestLike,
} from "./swRoutes";

const ORIGIN = "https://manovia.example";

function request(method = "GET", headers: string[] = []): RequestLike {
  return { method, headers: { has: (name: string) => headers.includes(name) } };
}

const url = (path: string, origin = ORIGIN): URL => new URL(path, origin);

describe("what the worker may cache", () => {
  it("caches the public helplines, with or without a region", () => {
    expect(isHelplinesRequest(url("/api/v1/crisis/resources"), request(), ORIGIN)).toBe(true);
    expect(isHelplinesRequest(url("/api/v1/crisis/resources?region=IN"), request(), ORIGIN)).toBe(
      true,
    );
  });

  it("caches the exercise catalogue and one exercise definition", () => {
    expect(isExercisesRequest(url("/api/v1/exercises"), request(), ORIGIN)).toBe(true);
    expect(isExercisesRequest(url("/api/v1/exercises/box-breathing"), request(), ORIGIN)).toBe(
      true,
    );
  });

  it("never caches personal or emotion-dependent exercise answers", () => {
    for (const path of [
      "/api/v1/exercises/history",
      "/api/v1/exercises/history?limit=20&offset=0",
      "/api/v1/exercises/recommendations?emotion=anxiety&intensity=high",
      "/api/v1/exercises/box-breathing/complete",
    ]) {
      expect(isExercisesRequest(url(path), request(), ORIGIN), path).toBe(false);
    }
  });

  it("refuses anything that carries a session, even on a public path", () => {
    const signedIn = request("GET", ["Authorization"]);
    expect(isHelplinesRequest(url(HELPLINES_PATH), signedIn, ORIGIN)).toBe(false);
    expect(isExercisesRequest(url(EXERCISES_PATH), signedIn, ORIGIN)).toBe(false);
  });

  it("refuses writes and other origins", () => {
    expect(isHelplinesRequest(url(HELPLINES_PATH), request("POST"), ORIGIN)).toBe(false);
    expect(isExercisesRequest(url(EXERCISES_PATH), request("PUT"), ORIGIN)).toBe(false);
    expect(isHelplinesRequest(url(HELPLINES_PATH, "https://evil.example"), request(), ORIGIN)).toBe(
      false,
    );
  });

  it("matches none of the personal API surfaces", () => {
    for (const path of [
      "/api/v1/mood",
      "/api/v1/mood/streak?tz=UTC",
      "/api/v1/journal?limit=50",
      "/api/v1/chat/sessions",
      "/api/v1/chat/memory",
      "/api/v1/plan/today",
      "/api/v1/insights/summary",
      "/api/v1/privacy/export",
      "/api/v1/auth/me",
      "/api/v1/crisis/assess",
    ]) {
      const target = url(path);
      expect(isHelplinesRequest(target, request(), ORIGIN), path).toBe(false);
      expect(isExercisesRequest(target, request(), ORIGIN), path).toBe(false);
    }
  });

  it("only keeps complete answers", () => {
    expect(isCacheableStatus(200)).toBe(true);
    for (const status of [0, 204, 206, 301, 304, 404, 500, 503]) {
      expect(isCacheableStatus(status), String(status)).toBe(false);
    }
  });

  it("warms exactly the two public URLs the app requests", () => {
    expect([...WARM_URLS].sort()).toEqual([EXERCISES_PATH, HELPLINES_PATH].sort());
    // Three caches, two of them warmed at install. The third (Day 41) holds
    // Private Mode's compiled artifacts and is filled at runtime, the first time
    // somebody turns Private Mode on: 191 KB of safety rules is not a background
    // download for everybody.
    expect(Object.values(CACHE_NAMES)).toEqual([
      "manovia-helplines-v1",
      "manovia-exercises-v1",
      "manovia-private-v1",
    ]);
  });
});

describe("which navigations get the offline app shell", () => {
  it("answers client-side routes", () => {
    for (const path of ["/", "/mood", "/exercises", "/settings", "/accessibility", "/x/y"]) {
      expect(isAppShellNavigation(url(path), ORIGIN), path).toBe(true);
    }
  });

  it("leaves the API, real files and other origins alone", () => {
    for (const path of [
      "/api/v1/ready",
      "/manifest.webmanifest",
      "/icons/icon-192.png",
      "/sw.js",
    ]) {
      expect(isAppShellNavigation(url(path), ORIGIN), path).toBe(false);
    }
    expect(isAppShellNavigation(url("/", "https://other.example"), ORIGIN)).toBe(false);
  });
});

describe("Private Mode's compiled artifacts (Day 41)", () => {
  it("caches the safety bundle and the model manifest", () => {
    expect(isPrivateAssetRequest(url("/private/safety-bundle.json"), request(), ORIGIN)).toBe(true);
    expect(isPrivateAssetRequest(url("/private/model-manifest.json"), request(), ORIGIN)).toBe(
      true,
    );
  });

  it("refuses model bytes, nested paths, writes, sessions and other origins", () => {
    // Model bytes go through the consented, hash-verified download manager into
    // IndexedDB. A silent copy in Cache Storage would bypass all of it.
    const refused: [string, RequestLike, string][] = [
      ["/private/models/emotion/onnx/model_quantized.onnx", request(), "a model file"],
      ["/private/nested/safety-bundle.json", request(), "a nested path"],
      ["/private/safety-bundle.json", request("POST"), "a write"],
      ["/private/safety-bundle.json", request("GET", ["Authorization"]), "a session"],
      ["/api/v1/chat/sessions", request(), "the chat API"],
    ];
    for (const [path, req, why] of refused) {
      expect(isPrivateAssetRequest(url(path), req, ORIGIN), `${path} (${why})`).toBe(false);
    }
    expect(
      isPrivateAssetRequest(
        url("/private/safety-bundle.json", "https://elsewhere.example"),
        request(),
        ORIGIN,
      ),
    ).toBe(false);
  });

  it("is not warmed at install: 191 KB of rules is for people who ask for it", () => {
    expect(WARM_URLS).toEqual([HELPLINES_PATH, EXERCISES_PATH]);
    expect(WARM_URLS.some((path) => path.startsWith("/private/"))).toBe(false);
    expect(CACHE_NAMES.privateAssets).toBe("manovia-private-v1");
  });
});

describe("the precache list", () => {
  it("leaves out the chart library, Insights and Private Mode, keeps everything else", async () => {
    const entries = [
      { url: "index.html", revision: "1", size: 1 },
      { url: "assets/index-abc.js", revision: null, size: 1 },
      { url: "assets/ExercisesPage-def.js", revision: null, size: 1 },
      { url: "assets/charts-123.js", revision: null, size: 1 },
      { url: "assets/InsightsPage-456.js", revision: null, size: 1 },
      { url: "assets/PrivateModeProvider-789.js", revision: null, size: 1 },
      { url: "manifest.webmanifest", revision: "2", size: 1 },
    ];
    const result = await filterPrecacheManifest(entries);
    expect(result.manifest.map((entry) => entry.url)).toEqual([
      "index.html",
      "assets/index-abc.js",
      "assets/ExercisesPage-def.js",
      "manifest.webmanifest",
    ]);
  });
});
