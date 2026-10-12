/// <reference lib="webworker" />

/**
 * Manovia service worker (Day 25, Workbox `injectManifest`).
 *
 * Caching rules — what is cached and what is deliberately NEVER cached:
 *
 * 1. **App shell & static assets** (`self.__WB_MANIFEST`): precached by Workbox
 *    with content-hash revisions, and `NavigationRoute` serves `/index.html` for
 *    every non-API route so `/chat`, `/mood`, `/exercises`, `/journal`,
 *    `/accessibility` and `/settings` open with no network.
 * 2. **Helplines** (`GET /api/v1/crisis/resources*`): `NetworkFirst` with a 3s
 *    timeout into `manovia-crisis-resources-v1`, plus an explicit warm-up fetch
 *    on `activate`. Online readers always verify the newest `helplines.json`;
 *    offline readers immediately get the last cached copy so "Need help now?"
 *    never opens an empty dialog.
 * 3. **Guided exercises** (`GET /api/v1/exercises` and `/api/v1/exercises/*`,
 *    excluding `/history`): `StaleWhileRevalidate` into `manovia-exercises-v1`,
 *    plus an explicit warm-up fetch on `activate`. Every step of Box breathing
 *    and 4-7-8 slow exhale is inside the catalogue response, so breathing works
 *    end-to-end offline.
 * 4. **Consent documents** (`GET /api/v1/consent/requirements`): `NetworkFirst`
 *    into `manovia-consent-v1`.
 * 5. **Personal data** (`/api/v1/journal*`, `/api/v1/chat*`, `/api/v1/mood*`,
 *    `/api/v1/privacy*`, `/api/v1/auth*`, `/api/v1/exercises/history`): NEVER
 *    cached by the service worker. Personal words and tokens belong in encrypted
 *    server storage or explicit client-side storage the user controls, never in
 *    shared CacheStorage.
 */

import { ExpirationPlugin } from "workbox-expiration";
import {
  cleanupOutdatedCaches,
  createHandlerBoundToURL,
  precacheAndRoute,
  type PrecacheEntry,
} from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { NetworkFirst, StaleWhileRevalidate } from "workbox-strategies";

declare let self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<PrecacheEntry | string>;
};

export const CRISIS_CACHE_NAME = "manovia-crisis-resources-v1";
export const EXERCISES_CACHE_NAME = "manovia-exercises-v1";
export const CONSENT_CACHE_NAME = "manovia-consent-v1";

const WARMUP_URLS: readonly [string, string][] = [
  ["/api/v1/crisis/resources", CRISIS_CACHE_NAME],
  ["/api/v1/exercises", EXERCISES_CACHE_NAME],
];

cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST ?? []);

// SPA navigation fallback: every route except `/api/*` resolves to the precached
// `/index.html` when the network is gone.
try {
  const navigationHandler = createHandlerBoundToURL("/index.html");
  registerRoute(
    new NavigationRoute(navigationHandler, {
      denylist: [/^\/api\//],
    }),
  );
} catch {
  // In unit tests or dev mode where `/index.html` is not in `__WB_MANIFEST`,
  // skip binding the precache handler rather than failing registration.
}

// 1. Crisis helplines: NetworkFirst (3s timeout) so fresh numbers win online
//    and cached numbers win offline.
registerRoute(
  ({ request, url }) =>
    request.method === "GET" && url.pathname.startsWith("/api/v1/crisis/resources"),
  new NetworkFirst({
    cacheName: CRISIS_CACHE_NAME,
    networkTimeoutSeconds: 3,
    plugins: [
      new ExpirationPlugin({
        maxEntries: 16,
        maxAgeSeconds: 30 * 24 * 60 * 60,
      }),
    ],
  }),
);

// 2. Guided exercises catalogue & recommendations (never `/history`, which is
//    per-user): StaleWhileRevalidate so exercises open immediately offline.
registerRoute(
  ({ request, url }) =>
    request.method === "GET" &&
    (url.pathname === "/api/v1/exercises" ||
      (url.pathname.startsWith("/api/v1/exercises/") &&
        !url.pathname.startsWith("/api/v1/exercises/history"))),
  new StaleWhileRevalidate({
    cacheName: EXERCISES_CACHE_NAME,
    plugins: [
      new ExpirationPlugin({
        maxEntries: 32,
        maxAgeSeconds: 30 * 24 * 60 * 60,
      }),
    ],
  }),
);

// 3. Public consent requirements document set.
registerRoute(
  ({ request, url }) =>
    request.method === "GET" && url.pathname === "/api/v1/consent/requirements",
  new NetworkFirst({
    cacheName: CONSENT_CACHE_NAME,
    networkTimeoutSeconds: 3,
    plugins: [
      new ExpirationPlugin({
        maxEntries: 4,
        maxAgeSeconds: 14 * 24 * 60 * 60,
      }),
    ],
  }),
);

async function warmCriticalCaches(): Promise<void> {
  await Promise.all(
    WARMUP_URLS.map(async ([url, cacheName]) => {
      try {
        const response = await fetch(url, { credentials: "omit" });
        if (response.ok) {
          const cache = await caches.open(cacheName);
          await cache.put(url, response.clone());
        }
      } catch {
        // Offline during activation: keep whatever is already in CacheStorage.
      }
    }),
  );
}

self.addEventListener("install", () => {
  void self.skipWaiting();
});

self.addEventListener("activate", (event: ExtendableEvent) => {
  event.waitUntil(
    (async () => {
      await self.clients.claim();
      await warmCriticalCaches();
    })(),
  );
});

async function notifyClientsToFlushMoodQueue(): Promise<void> {
  const windowClients = await self.clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });
  for (const client of windowClients) {
    client.postMessage({ type: "MANOVIA_FLUSH_MOOD_QUEUE" });
  }
}

self.addEventListener("message", (event: ExtendableMessageEvent) => {
  const data = event.data as { type?: string } | null;
  if (data?.type === "SKIP_WAITING") {
    void self.skipWaiting();
  } else if (data?.type === "WARM_OFFLINE_CACHE") {
    event.waitUntil(warmCriticalCaches());
  } else if (data?.type === "SYNC_MOOD_QUEUE") {
    event.waitUntil(notifyClientsToFlushMoodQueue());
  }
});

self.addEventListener("sync", ((event: Event & { tag?: string; waitUntil?: (p: Promise<unknown>) => void }) => {
  if (event.tag === "manovia-mood-sync" && typeof event.waitUntil === "function") {
    event.waitUntil(notifyClientsToFlushMoodQueue());
  }
}) as EventListener);
