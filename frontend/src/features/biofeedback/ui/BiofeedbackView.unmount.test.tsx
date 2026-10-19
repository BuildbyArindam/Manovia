/**
 * Regression for a CI flake in the biofeedback view (found on Day 47, caused by
 * Day 42 code). Kept in its own file because it mocks the summary store, while
 * `BiofeedbackView.test.tsx` exercises the real encrypted one.
 *
 * CI run 39337225868 failed the frontend job with:
 *
 *   Unhandled error :: ReferenceError: window is not defined
 *    ❯ getCurrentEventPriority node_modules/react-dom/cjs/react-dom.development.js
 *    ❯ dispatchSetState node_modules/react-dom/cjs/react-dom.development.js
 *    ❯ src/features/biofeedback/ui/BiofeedbackView.tsx:122
 *   "This error originated in BiofeedbackView.test.tsx ... while it was running."
 *
 * Every render of the view starts an IndexedDB read in an effect. When the last
 * test in the file finishes, vitest tears the jsdom environment down while that
 * read is still in flight; it then rejects (no storage left), and the catch
 * branch called `setTrend([])` — React's update path reads `window.event`, and
 * `window` was gone. Which test lost the race depended on timing, so the job
 * passed three runs in a row and failed the fourth.
 *
 * The view now ignores settlements that land after unmount. These tests pin both
 * halves of that: a late rejection must not touch a dead environment, and a
 * settlement while mounted must still update the trend (the guard is not simply
 * "never render the trend").
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/lib/i18n/I18nProvider";

import type { HeartRateSource } from "../ble/heartRateDevice";
import type { SummaryStore } from "../store/summaryStore";
import { BiofeedbackView } from "./BiofeedbackView";

type StoredEntry = { id: string; createdAt: string; summary: unknown };

/** The pending `loadAll` of the most recently rendered view, if any. */
let pending: {
  resolve: (entries: StoredEntry[]) => void;
  reject: (reason: unknown) => void;
} | null = null;

vi.mock("../store/summaryStore", () => ({
  openSummaryStore: (): Promise<SummaryStore> =>
    Promise.resolve({
      loadAll: () =>
        new Promise<StoredEntry[]>((resolve, reject) => {
          pending = { resolve, reject };
        }),
    } as unknown as SummaryStore),
}));

/** Unsupported browser: the view renders its explanation and never connects. */
const noBluetooth: HeartRateSource = {
  supported: false,
  connect: () => Promise.reject(new Error("connect must not be called in these tests")),
};

function renderView() {
  return render(
    <I18nProvider>
      <BiofeedbackView source={noBluetooth} now={() => 0} />
    </I18nProvider>,
  );
}

/** Let the promise chain behind `loadAll` run to completion. Microtasks only:
 *  no timers, so nothing can outlive the test and flake the way this one did. */
async function flushMicrotasks(hops = 12): Promise<void> {
  for (let i = 0; i < hops; i += 1) {
    await Promise.resolve();
  }
}

describe("BiofeedbackView trend read after unmount", () => {
  it("ignores a store rejection that lands once the view is gone", async () => {
    const { unmount } = renderView();
    // `openSummaryStore()` itself is a promise, so the view reaches `loadAll()`
    // one microtask after the effect runs.
    await flushMicrotasks();
    expect(pending).not.toBeNull();
    const inFlight = pending;
    unmount();

    // Reproduce the teardown rather than approximating it: vitest removes the
    // jsdom globals when a file finishes, so `window` is not merely undefined —
    // the binding is gone, which is why React threw a ReferenceError (reading a
    // property of `undefined` would have been a TypeError).
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
    // @ts-expect-error - deliberately remove the environment under test
    delete globalThis.window;
    expect("window" in globalThis).toBe(false);
    try {
      inFlight?.reject(new Error("IndexedDB went away with the environment"));
      await flushMicrotasks();
    } finally {
      if (descriptor) {
        Object.defineProperty(globalThis, "window", descriptor);
      }
    }
    // The environment is back for every later test in the file. Without the
    // unmount guard this run fails exactly as CI run 39337225868 did: vitest
    // reports an "Unhandled Errors / Unhandled Rejection: ReferenceError: window
    // is not defined" section and exits 1, while the two `it` blocks themselves
    // still read as passed — the throw happens on a floating promise after the
    // test body returned, so the gate is the exit code, not an assertion.
    // Verified: guards removed -> exit 1 with that ReferenceError; guards in
    // place -> exit 0 and no such error.
    expect(globalThis.window).toBeDefined();
  });

  it("still renders the trend when the store resolves while mounted", async () => {
    renderView();
    await flushMicrotasks();
    expect(pending).not.toBeNull();
    expect(screen.getByTestId("trend-empty")).toBeTruthy();

    pending?.resolve([
      {
        id: "session-1",
        createdAt: "2026-10-19T09:00:00.000Z",
        summary: {
          id: "session-1",
          startedAt: "2026-10-19T09:00:00.000Z",
          durationSec: 180,
          preMood: 2,
          postMood: 3,
          pre: { rmssdMs: 28 },
          post: { rmssdMs: 41 },
        },
      },
    ]);

    await waitFor(() => expect(screen.getByTestId("trend-table")).toBeTruthy());
    expect(screen.queryByTestId("trend-empty")).toBeNull();
  });
});
