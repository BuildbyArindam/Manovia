/**
 * What the service worker is allowed to keep, in one reviewable place (Day 25).
 *
 * The service worker (`src/sw/sw.ts`) imports these predicates, and so do the
 * unit tests, so the privacy boundary is asserted rather than described. The
 * rule is short:
 *
 * * **The app shell** (HTML, JS, CSS, icons, manifest) is precached at install.
 * * **Two public, read-only API answers** are cached at runtime, because they
 *   are what someone needs when the network is gone: the crisis helplines
 *   (`GET /api/v1/crisis/resources`) and the exercise catalogue
 *   (`GET /api/v1/exercises`, `GET /api/v1/exercises/{id}`). Both endpoints are
 *   public on purpose — no sign-in, no `Authorization` header, nothing personal
 *   in the response.
 * * **Nothing else from the API is ever cached.** Not the journal, not mood
 *   check-ins, not chat, not the plan, not insights. Those responses are
 *   decrypted personal data; a copy in Cache Storage would outlive sign-out,
 *   survive "delete my account" and be readable by anyone holding the device.
 *   A request that carries an `Authorization` header is refused here even when
 *   its path looks public, so a future endpoint cannot leak in by accident.
 *
 * This module is deliberately free of DOM and worker types: it is compiled into
 * both the page bundle (tests) and the worker bundle.
 */

/** Cache names. Versioned so a contract change can drop the old cache. */
export const CACHE_NAMES = {
  helplines: "manovia-helplines-v1",
  exercises: "manovia-exercises-v1",
} as const;

/** The API prefix every route below lives under. */
export const API_PREFIX = "/api/v1";

/** Helplines: network first (numbers change), cached copy when offline. */
export const HELPLINES_PATH = `${API_PREFIX}/crisis/resources`;

/** Exercises: the catalogue and one definition. Recommendations are not cached. */
export const EXERCISES_PATH = `${API_PREFIX}/exercises`;

/**
 * URLs warmed when the worker installs, so "Need help now?" and breathing work
 * offline even for someone who has never opened them while online. These are
 * the exact URLs the app requests (`fetchCrisisResources()` with no region and
 * `fetchExercises()`), because the cache is keyed by URL.
 */
export const WARM_URLS: readonly string[] = [HELPLINES_PATH, EXERCISES_PATH];

/** How long a cached helpline answer may be served offline (30 days). */
export const HELPLINES_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;

/** How long a cached exercise answer may be served offline (60 days). */
export const EXERCISES_MAX_AGE_SECONDS = 60 * 24 * 60 * 60;

/**
 * How long to wait for the network before answering a helpline request from
 * the cache. Short on purpose: on a weak connection, a person who pressed
 * "Need help now?" should see numbers in seconds, not wait for a timeout.
 */
export const HELPLINES_NETWORK_TIMEOUT_SECONDS = 3;

/** The minimal request shape the predicates need (a `Request` satisfies it). */
export interface RequestLike {
  method: string;
  headers: { has(name: string): boolean };
}

function sameOrigin(url: URL, origin: string): boolean {
  return url.origin === origin;
}

/** True only for an unauthenticated GET. */
function isPublicRead(request: RequestLike): boolean {
  return request.method === "GET" && !request.headers.has("Authorization");
}

/** `GET /api/v1/crisis/resources` (any `?region=`), unauthenticated, same origin. */
export function isHelplinesRequest(url: URL, request: RequestLike, origin: string): boolean {
  return sameOrigin(url, origin) && isPublicRead(request) && url.pathname === HELPLINES_PATH;
}

/**
 * `GET /api/v1/exercises` or `GET /api/v1/exercises/{id}` — the catalogue and
 * one definition. History, completion and recommendations are excluded: the
 * first two are personal, the third depends on the emotion somebody reported.
 */
export function isExercisesRequest(url: URL, request: RequestLike, origin: string): boolean {
  if (!sameOrigin(url, origin) || !isPublicRead(request)) {
    return false;
  }
  if (url.pathname === EXERCISES_PATH) {
    return true;
  }
  const match = /^\/api\/v1\/exercises\/([^/]+)$/.exec(url.pathname);
  if (match === null) {
    return false;
  }
  const id = match[1] ?? "";
  return id !== "history" && id !== "recommendations";
}

/**
 * Navigations that may be answered with the cached app shell. Everything that
 * is not an API call or a file with an extension is a client-side route.
 */
export function isAppShellNavigation(url: URL, origin: string): boolean {
  if (!sameOrigin(url, origin)) {
    return false;
  }
  if (url.pathname.startsWith("/api/")) {
    return false;
  }
  // `/manifest.webmanifest`, `/icons/x.png`, `/sw.js`: real files, not routes.
  const lastSegment = url.pathname.split("/").pop() ?? "";
  return !lastSegment.includes(".");
}

/**
 * A cached response is only worth keeping when it is a complete, successful
 * answer. `0` (opaque) never happens for same-origin requests and is refused.
 */
export function isCacheableStatus(status: number): boolean {
  return status === 200;
}
