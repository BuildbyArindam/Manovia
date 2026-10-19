import { beforeEach, describe, expect, it } from "vitest";

import {
  AROUSAL_STORAGE_KEY,
  HIGH_AROUSAL_THRESHOLD,
  HIGH_AROUSAL_WINDOW_MS,
  SUGGESTION_DISMISS_MS,
  dismissSuggestion,
  highArousalCount,
  isHighArousal,
  recordCheckIn,
  shouldSuggestSimplifiedMode,
  suggestionDismissed,
} from "./arousal";

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.UTC(2026, 9, 19, 9, 0, 0);

function storage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (key: string) => map.get(key) ?? null,
    key: (index: number) => [...map.keys()][index] ?? null,
    removeItem: (key: string) => {
      map.delete(key);
    },
    setItem: (key: string, value: string) => {
      map.set(key, value);
    },
  };
}

describe("high-arousal detection", () => {
  it("needs both activation and unpleasantness", () => {
    expect(isHighArousal({ valence: 2, energy: 4 })).toBe(true);
    expect(isHighArousal({ valence: 1, energy: 5 })).toBe(true);
    expect(isHighArousal({ valence: 3, energy: 5 })).toBe(false);
    expect(isHighArousal({ valence: 1, energy: 3 })).toBe(false);
  });

  it("stores timestamps only, never the check-in", () => {
    const local = storage();
    recordCheckIn({ valence: 1, energy: 5 }, NOW, local);
    const raw = local.getItem(AROUSAL_STORAGE_KEY) ?? "";
    expect(JSON.parse(raw)).toEqual([NOW]);
    expect(raw).not.toContain("valence");
    expect(raw).not.toContain("energy");
  });

  it("does not record a calmer check-in", () => {
    const local = storage();
    recordCheckIn({ valence: 4, energy: 2 }, NOW, local);
    expect(local.getItem(AROUSAL_STORAGE_KEY)).toBeNull();
    expect(highArousalCount(NOW, local)).toBe(0);
  });
});

describe("the optional suggestion", () => {
  let local: Storage;

  beforeEach(() => {
    local = storage();
  });

  it(`appears only after ${HIGH_AROUSAL_THRESHOLD} activated, unpleasant check-ins`, () => {
    recordCheckIn({ valence: 1, energy: 5 }, NOW - 2 * DAY, local);
    recordCheckIn({ valence: 2, energy: 4 }, NOW - DAY, local);
    expect(shouldSuggestSimplifiedMode(NOW, local)).toBe(false);
    recordCheckIn({ valence: 1, energy: 4 }, NOW, local);
    expect(shouldSuggestSimplifiedMode(NOW, local)).toBe(true);
  });

  it("forgets events older than two weeks", () => {
    recordCheckIn({ valence: 1, energy: 5 }, NOW - HIGH_AROUSAL_WINDOW_MS - DAY, local);
    recordCheckIn({ valence: 1, energy: 5 }, NOW - HIGH_AROUSAL_WINDOW_MS - DAY, local);
    recordCheckIn({ valence: 1, energy: 5 }, NOW - HIGH_AROUSAL_WINDOW_MS - DAY, local);
    expect(shouldSuggestSimplifiedMode(NOW, local)).toBe(false);
  });

  it("never switches anything by itself, and stays dismissed for a month", () => {
    for (let i = 0; i < HIGH_AROUSAL_THRESHOLD; i += 1) {
      recordCheckIn({ valence: 1, energy: 5 }, NOW, local);
    }
    expect(shouldSuggestSimplifiedMode(NOW, local)).toBe(true);
    dismissSuggestion(NOW, local);
    expect(suggestionDismissed(NOW, local)).toBe(true);
    expect(shouldSuggestSimplifiedMode(NOW + DAY, local)).toBe(false);
    // After the month, the offer may return — but only because there are new
    // high-arousal check-ins inside the window, never from the old ones.
    const later = NOW + SUGGESTION_DISMISS_MS + 1;
    for (let i = 0; i < HIGH_AROUSAL_THRESHOLD; i += 1) {
      recordCheckIn({ valence: 1, energy: 5 }, later, local);
    }
    expect(shouldSuggestSimplifiedMode(later, local)).toBe(true);
  });

  it("is silent when storage is blocked", () => {
    expect(shouldSuggestSimplifiedMode(NOW, null)).toBe(false);
    expect(() => {
      recordCheckIn({ valence: 1, energy: 5 }, NOW, null);
      dismissSuggestion(NOW, null);
    }).not.toThrow();
  });
});
