/**
 * Switch jsdom's `navigator.onLine` and fire the matching window event, the
 * way a browser does when the connection drops or returns (Day 25 tests).
 */

import { act } from "@testing-library/react";

export function setOnline(online: boolean): void {
  Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => online });
  act(() => {
    window.dispatchEvent(new Event(online ? "online" : "offline"));
  });
}

/** Restore the jsdom default (online) without firing an event. */
export function resetOnline(): void {
  Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => true });
}
