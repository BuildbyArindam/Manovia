/**
 * Is this device online? (Day 25)
 *
 * `navigator.onLine` is a hint, not a promise: `true` means "connected to some
 * network", not "the API will answer". So the app never *relies* on it being
 * true — every request still handles failure — but it does trust `false`, which
 * is reliable, to skip a request that cannot succeed and say so up front.
 */

import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

/** The current hint; `true` where the browser cannot tell. */
export function isOnline(): boolean {
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

/** Re-renders when the device goes offline or comes back. */
export function useOnlineStatus(): boolean {
  return useSyncExternalStore(subscribe, isOnline, () => true);
}
