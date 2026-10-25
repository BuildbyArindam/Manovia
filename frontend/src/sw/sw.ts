/**
 * Manovia's service worker (Day 25), built by `vite-plugin-pwa` with the
 * `injectManifest` strategy so every routing decision is code we can read.
 *
 * What it does, and nothing more:
 *
 * 1. **Precaches the app shell** — the HTML document, the JavaScript and CSS
 *    chunks, the icons and the manifest — so the app opens with no network.
 *    The list (`self.__WB_MANIFEST`) is generated at build time with a content
 *    revision per file; heavy chunks that are useless offline are filtered out
 *    in `vite.config.ts`.
 * 2. **Answers client-side navigations with that shell**, so `/mood` or
 *    `/exercises` typed into the address bar works offline too.
 * 3. **Keeps two public API answers** so the two things a person may need most
 *    when the network is gone still work: the crisis helplines behind "Need
 *    help now?" (network first, cached copy offline) and the exercise catalogue
 *    the breathing guide runs from (stale-while-revalidate). Both are warmed at
 *    install time, so they work offline even if they were never opened online.
 *
 * What it deliberately does **not** do: cache any authenticated or personal
 * API response, queue writes, or run background sync. Mood check-ins made
 * offline are queued by the page (`features/mood/outbox.ts`), where the session
 * lives; the worker never sees a token. See `src/pwa/swRoutes.ts` for the rules
 * and ADR 0025 for the reasoning.
 */

/// <reference lib="webworker" />

import { clientsClaim } from "workbox-core";
import { ExpirationPlugin } from "workbox-expiration";
import {
  cleanupOutdatedCaches,
  createHandlerBoundToURL,
  precacheAndRoute,
} from "workbox-precaching";
import { registerRoute } from "workbox-routing";
import { NetworkFirst, StaleWhileRevalidate } from "workbox-strategies";
import type { WorkboxPlugin } from "workbox-core/types";

import { appShellUrl } from "../lib/basePath";
import {
  CACHE_NAMES,
  EXERCISES_MAX_AGE_SECONDS,
  HELPLINES_MAX_AGE_SECONDS,
  HELPLINES_NETWORK_TIMEOUT_SECONDS,
  HELPLINES_PATH,
  PRIVATE_ASSETS_MAX_AGE_SECONDS,
  WARM_URLS,
  isAppShellNavigation,
  isCacheableStatus,
  isExercisesRequest,
  isHelplinesRequest,
  isPrivateAssetRequest,
} from "../pwa/swRoutes";

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<{ url: string; revision: string | null }>;
};

/** Only a complete 200 answer is ever written to a runtime cache. */
const okOnly: WorkboxPlugin = {
  cacheWillUpdate: async ({ response }) => (isCacheableStatus(response.status) ? response : null),
};

// ---------------------------------------------------------------------------
// 1. The app shell.
precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

// ---------------------------------------------------------------------------
// 2. Client-side routes answer with the precached document.
// Resolved against the worker's own URL (`…/sw.js` → `…/index.html`) so it
// matches the base-relative precache key: a hardcoded "/index.html" resolves
// outside the scope on a /Manovia/ deployment and throws at install time.
const shellHandler = createHandlerBoundToURL(appShellUrl(self.location));
registerRoute(
  ({ request, url }) =>
    request.mode === "navigate" && isAppShellNavigation(url, self.location.origin),
  shellHandler,
);

// ---------------------------------------------------------------------------
// 3a. Helplines: the network when it answers quickly, the saved copy when not.
registerRoute(
  ({ request, url }) => isHelplinesRequest(url, request, self.location.origin),
  new NetworkFirst({
    cacheName: CACHE_NAMES.helplines,
    networkTimeoutSeconds: HELPLINES_NETWORK_TIMEOUT_SECONDS,
    matchOptions: { ignoreVary: true },
    plugins: [
      okOnly,
      new ExpirationPlugin({ maxEntries: 20, maxAgeSeconds: HELPLINES_MAX_AGE_SECONDS }),
    ],
  }),
);

// 3b. Exercises: answer from the cache at once, refresh it in the background.
registerRoute(
  ({ request, url }) => isExercisesRequest(url, request, self.location.origin),
  new StaleWhileRevalidate({
    cacheName: CACHE_NAMES.exercises,
    matchOptions: { ignoreVary: true },
    plugins: [
      okOnly,
      new ExpirationPlugin({ maxEntries: 60, maxAgeSeconds: EXERCISES_MAX_AGE_SECONDS }),
    ],
  }),
);

// ---------------------------------------------------------------------------
// 3c. Private Mode's compiled artifacts (Day 41): the safety bundle and the model
// manifest. Network first, because a deploy that changes a safety rule must reach
// the device the next time it is online — serving a stale rule set from cache
// would be a safety regression, not a performance win. The cached copy is what
// makes Private Mode boot with no network at all.
//
// Not warmed at install, unlike the two above: 191 KB of compiled rules is only
// useful to somebody who turns Private Mode on, and downloading it for everyone
// in the background is exactly what the bundle budget exists to prevent.
registerRoute(
  ({ request, url }) => isPrivateAssetRequest(url, request, self.location.origin),
  new NetworkFirst({
    cacheName: CACHE_NAMES.privateAssets,
    networkTimeoutSeconds: HELPLINES_NETWORK_TIMEOUT_SECONDS,
    matchOptions: { ignoreVary: true },
    plugins: [
      okOnly,
      new ExpirationPlugin({ maxEntries: 4, maxAgeSeconds: PRIVATE_ASSETS_MAX_AGE_SECONDS }),
    ],
  }),
);

// ---------------------------------------------------------------------------
// Warm the two offline caches at install. Best effort by design: an install
// that happens offline must still succeed (the runtime routes above fill the
// caches the next time the app is online), so a failure here is swallowed.
async function warmOfflineCaches(): Promise<void> {
  await Promise.all(
    WARM_URLS.map(async (path) => {
      try {
        const response = await fetch(path, { credentials: "same-origin" });
        if (!isCacheableStatus(response.status)) {
          return;
        }
        const cacheName = path === HELPLINES_PATH ? CACHE_NAMES.helplines : CACHE_NAMES.exercises;
        const cache = await caches.open(cacheName);
        await cache.put(path, response);
      } catch {
        // Offline at install time: nothing to warm yet.
      }
    }),
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(warmOfflineCaches());
});

// Take control of the page that registered us on the very first install, so
// offline works without a reload. Later updates wait for the reader to accept
// them (see `src/pwa/registerServiceWorker.ts`): reloading somebody's page in
// the middle of a breathing exercise or a journal entry is not an update, it
// is an interruption.
clientsClaim();

self.addEventListener("message", (event) => {
  const data: unknown = event.data;
  if (
    typeof data === "object" &&
    data !== null &&
    (data as { type?: unknown }).type === "SKIP_WAITING"
  ) {
    void self.skipWaiting();
  }
});
