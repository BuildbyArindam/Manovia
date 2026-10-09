import { vi } from "vitest";

export interface StubbedMediaQuery {
  /** Change what the query reports, as the OS would. */
  emit: (matches: boolean) => void;
}

/**
 * Replace `window.matchMedia` with a controllable stub.
 *
 * jsdom has none, and the app's layout, palette and motion behaviour all hang
 * off media queries — so a test that cares installs one of these.
 */
export function stubMatchMedia(initial: boolean): StubbedMediaQuery {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const state = { matches: initial };
  const matchMedia = vi.fn((query: string) => ({
    // A getter, so emitting a change is visible to the listener that reads
    // `list.matches` — the way a real MediaQueryList behaves.
    get matches(): boolean {
      return state.matches;
    },
    media: query,
    onchange: null,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.add(listener);
    },
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.delete(listener);
    },
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
  vi.stubGlobal("matchMedia", matchMedia);
  return {
    emit: (matches: boolean) => {
      state.matches = matches;
      for (const listener of listeners) {
        listener({ matches } as MediaQueryListEvent);
      }
    },
  };
}
