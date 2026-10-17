import { describe, expect, it } from "vitest";

import {
  MAX_HISTORY,
  emptyLibrary,
  getFavourite,
  pushHistory,
  readLibrary,
  removeFavourite,
  saveFavourite,
  writeLibrary,
} from "./library";
import type { MeditationScript } from "./types";

function scriptWith(hash: string): MeditationScript {
  return {
    version: 1,
    id: `med_${hash.slice(0, 8)}`,
    content_hash: hash,
    theme: "calm",
    resolved_theme: "calm",
    language: "en",
    duration_seconds: 180,
    voice_speed: 1.0,
    context: "seated",
    eyes_open: false,
    preferences: [],
    seed: 1,
    word_count: 240,
    estimated_duration_s: 180,
    sections: [],
    transcript: "You can stop at any time.",
    safety: {
      safety_preface: true,
      stop_cue: true,
      eyes_open: false,
      driving_warning: false,
      eyes_closed_allowed: true,
    },
  };
}

function memoryStorage(): Storage {
  let data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
    clear: () => {
      data = new Map();
    },
    key: (index: number) => [...data.keys()][index] ?? null,
    get length() {
      return data.size;
    },
  };
}

describe("meditation library (local storage)", () => {
  it("saves a favourite and re-saving the same script replaces, not duplicates", () => {
    const state = saveFavourite(emptyLibrary(), scriptWith("a".repeat(64)), "A 3-minute calm", "soft-rain");
    expect(state.entries).toHaveLength(1);
    const again = saveFavourite(state, scriptWith("a".repeat(64)), "Renamed", "night-calm");
    expect(again.entries).toHaveLength(1);
    expect(again.entries[0]?.label).toBe("Renamed");
    expect(again.entries[0]?.palette_id).toBe("night-calm");
    expect(getFavourite(again, "a".repeat(64))?.label).toBe("Renamed");
  });

  it("removes favourites by content hash", () => {
    let state = saveFavourite(emptyLibrary(), scriptWith("b".repeat(64)), "One", "warm-dawn");
    state = saveFavourite(state, scriptWith("c".repeat(64)), "Two", "deep-forest");
    expect(state.entries).toHaveLength(2);
    state = removeFavourite(state, "b".repeat(64));
    expect(state.entries).toHaveLength(1);
    expect(state.entries[0]?.script.content_hash).toBe("c".repeat(64));
  });

  it("keeps a bounded recent-history of composed scripts", () => {
    let state = emptyLibrary();
    for (let i = 0; i < MAX_HISTORY + 5; i += 1) {
      state = pushHistory(state, `hash-${i}`);
    }
    expect(state.history).toHaveLength(MAX_HISTORY);
    expect(state.history[0]).toBe(`hash-${MAX_HISTORY + 4}`);
    // Pushing the same hash again does not duplicate it.
    const onceMore = pushHistory(state, state.history[0] ?? "none");
    expect(onceMore.history).toHaveLength(MAX_HISTORY);
  });

  it("round-trips through storage and survives corrupt data", () => {
    const storage = memoryStorage();
    const state = saveFavourite(emptyLibrary(), scriptWith("d".repeat(64)), "Saved", "warm-dawn");
    writeLibrary(state, storage);
    expect(readLibrary(storage).entries).toHaveLength(1);

    storage.setItem("manovia.meditations.library.v1", "{not json");
    expect(readLibrary(storage).entries).toHaveLength(0);
    storage.setItem("manovia.meditations.library.v1", JSON.stringify({ entries: "nope" }));
    expect(readLibrary(storage).entries).toHaveLength(0);
  });

  it("keeps working when storage is blocked", () => {
    const blocked: Storage = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
      removeItem: () => undefined,
      clear: () => undefined,
      key: () => null,
      length: 0,
    };
    expect(() => readLibrary(blocked)).not.toThrow();
    expect(() =>
      writeLibrary(saveFavourite(emptyLibrary(), scriptWith("e".repeat(64)), "X", "warm-dawn"), blocked),
    ).not.toThrow();
  });
});
