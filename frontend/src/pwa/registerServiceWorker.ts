/**
 * Service worker registration and the update flow (Day 25).
 *
 * Production builds only: the dev server and the unit tests never register a
 * worker, so a cached shell can never hide a code change during development.
 *
 * Updates are offered, never forced. When a new version has been downloaded the
 * app shows a small "a new version is ready" notice and waits for the reader to
 * press Reload. Swapping the code underneath somebody halfway through a
 * breathing exercise or a journal entry would be an interruption dressed up as
 * a feature (and an unexpected change of context, WCAG 3.2).
 */

import { useSyncExternalStore } from "react";

export interface PwaState {
  /** A new version is waiting; reloading will activate it. */
  needRefresh: boolean;
  /** The worker has precached the app shell: it now opens offline. */
  offlineReady: boolean;
}

let state: PwaState = { needRefresh: false, offlineReady: false };
const listeners = new Set<() => void>();
let updateServiceWorker: ((reloadPage?: boolean) => Promise<void>) | null = null;

function setState(next: Partial<PwaState>): void {
  state = { ...state, ...next };
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getPwaState(): PwaState {
  return state;
}

export function usePwaState(): PwaState {
  return useSyncExternalStore(subscribe, getPwaState, getPwaState);
}

/** Register the worker. Safe to call anywhere; a no-op outside production. */
export async function registerServiceWorker(): Promise<void> {
  if (
    !import.meta.env.PROD ||
    typeof navigator === "undefined" ||
    !("serviceWorker" in navigator)
  ) {
    return;
  }
  const { registerSW } = await import("virtual:pwa-register");
  updateServiceWorker = registerSW({
    onNeedRefresh: () => {
      setState({ needRefresh: true });
    },
    onOfflineReady: () => {
      setState({ offlineReady: true });
    },
    onRegisterError: (error: unknown) => {
      // Not fatal: the app works online without a worker.
      console.warn("Manovia: service worker registration failed", error);
    },
  });
}

/** Activate the waiting version and reload — only ever on the reader's request. */
export async function applyUpdate(): Promise<void> {
  if (updateServiceWorker !== null) {
    await updateServiceWorker(true);
  }
}

export function dismissUpdate(): void {
  setState({ needRefresh: false });
}

/** True once a worker controls this page, i.e. the app will open offline. */
export function isControlledByServiceWorker(): boolean {
  return (
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    navigator.serviceWorker.controller !== null
  );
}

/** Test seam: reset module state between tests. */
export function __resetPwaStateForTests(next: Partial<PwaState> = {}): void {
  state = { needRefresh: false, offlineReady: false, ...next };
  for (const listener of listeners) {
    listener();
  }
}
