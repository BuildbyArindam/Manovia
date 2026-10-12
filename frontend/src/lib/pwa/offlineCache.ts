/**
 * Client-side offline cache & Service Worker lifecycle (Day 25).
 *
 * Why two layers (Workbox CacheStorage in `src/sw.ts` + validated JSON
 * snapshots in `localStorage` here)?
 *
 * - In a production build (`npm run build`), the Workbox service worker
 *   precaches the app shell and serves `/api/v1/crisis/resources` and
 *   `/api/v1/exercises` from `CacheStorage`.
 * - On the very first visit in a tab (before a newly installed service worker
 *   has controlled the first navigation, or on the Vite dev server), a person
 *   who loads Manovia and then loses connectivity still needs "Need help now?"
 *   and the breathing exercises to work immediately. Warming both `queryClient`
 *   and a validated local snapshot from the live backend on boot guarantees
 *   that:
 *     1. No helpline number is ever hard-coded in frontend source code
 *        (AGENTS.md safety rule 6: `backend/app/content/helplines.json` is the
 *        single source of truth).
 *     2. Once the app has loaded online at least once, going offline at any
 *        moment — even before opening the help modal or visiting `/exercises`
 *        — serves the exact backend-verified helplines and exercises.
 */

import type { QueryClient } from "@tanstack/react-query";

import type { ExerciseListResponse } from "@/features/exercises/types";
import type { CrisisResourcesResponse } from "@/lib/endpoints";

export const OFFLINE_CRISIS_STORAGE_KEY = "manovia.offline.crisis.v1";
export const OFFLINE_EXERCISES_STORAGE_KEY = "manovia.offline.exercises.v1";

interface CrisisCacheMap {
  [regionKey: string]: CrisisResourcesResponse;
}

function regionCacheKey(region?: string | null): string {
  return region && region.trim() !== "" ? region.trim().toUpperCase() : "__ALL__";
}

export function readCachedCrisisResources(
  region?: string | null,
  storage: Storage = window.localStorage,
): CrisisResourcesResponse | null {
  try {
    const raw = storage.getItem(OFFLINE_CRISIS_STORAGE_KEY);
    if (raw === null) {
      return null;
    }
    const parsed = JSON.parse(raw) as CrisisCacheMap | null;
    if (typeof parsed !== "object" || parsed === null) {
      return null;
    }
    const exact = parsed[regionCacheKey(region)];
    if (exact && Array.isArray(exact.resources)) {
      return exact;
    }
    const fallbackAll = parsed["__ALL__"];
    if (fallbackAll && Array.isArray(fallbackAll.resources)) {
      return fallbackAll;
    }
    return null;
  } catch {
    return null;
  }
}

export function writeCachedCrisisResources(
  response: CrisisResourcesResponse,
  region?: string | null,
  storage: Storage = window.localStorage,
): void {
  try {
    const raw = storage.getItem(OFFLINE_CRISIS_STORAGE_KEY);
    const existing: CrisisCacheMap =
      raw !== null ? ((JSON.parse(raw) as CrisisCacheMap | null) ?? {}) : {};
    existing[regionCacheKey(region)] = response;
    storage.setItem(OFFLINE_CRISIS_STORAGE_KEY, JSON.stringify(existing));
  } catch {
    // Quota or private browsing: TanStack Query still holds the in-memory copy.
  }
}

export function readCachedExercises(
  storage: Storage = window.localStorage,
): ExerciseListResponse | null {
  try {
    const raw = storage.getItem(OFFLINE_EXERCISES_STORAGE_KEY);
    if (raw === null) {
      return null;
    }
    const parsed = JSON.parse(raw) as ExerciseListResponse | null;
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      Array.isArray(parsed.exercises) &&
      parsed.exercises.length > 0
    ) {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

export function writeCachedExercises(
  response: ExerciseListResponse,
  storage: Storage = window.localStorage,
): void {
  try {
    storage.setItem(OFFLINE_EXERCISES_STORAGE_KEY, JSON.stringify(response));
  } catch {
    // Quota or private browsing: TanStack Query still holds the in-memory copy.
  }
}

/**
 * Register the Workbox service worker (`/sw.js`) in browser environments.
 *
 * Skipped under Vitest (`MODE === "test"`) so unit tests never try to register
 * a worker against jsdom.
 */
export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (
    typeof window === "undefined" ||
    typeof navigator === "undefined" ||
    !("serviceWorker" in navigator) ||
    import.meta.env.MODE === "test"
  ) {
    return null;
  }

  try {
    const registration = await navigator.serviceWorker.register("/sw.js", {
      scope: "/",
    });
    if (registration.active) {
      registration.active.postMessage({ type: "WARM_OFFLINE_CACHE" });
    }
    return registration;
  } catch {
    // Service worker registration can fail on non-HTTPS origins or restricted
    // webviews; the client-side cache warmup still protects offline usage.
    return null;
  }
}

/**
 * Warm the TanStack Query cache and local offline snapshots for helplines and
 * exercises while online, so opening "Need help now?" or `/exercises` after
 * losing connectivity works even if the user never visited those surfaces
 * before going offline.
 */
export async function warmOfflineResources(
  queryClient: QueryClient,
  fetchCrisis: () => Promise<CrisisResourcesResponse>,
  fetchExerciseCatalog: () => Promise<ExerciseListResponse>,
): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return;
  }
  await Promise.allSettled([
    queryClient.prefetchQuery({
      queryKey: ["crisis-resources", null],
      queryFn: fetchCrisis,
      staleTime: Number.POSITIVE_INFINITY,
    }),
    queryClient.prefetchQuery({
      queryKey: ["exercises", "catalog"],
      queryFn: fetchExerciseCatalog,
      staleTime: Number.POSITIVE_INFINITY,
    }),
  ]);
}
