import { describe, expect, it } from "vitest";

import {
  DEFAULT_SENSORY_PROFILE,
  SENSORY_STORAGE_KEY,
  SENSORY_PROFILE_VERSION,
  FONT_SCALE,
} from "./types";
import {
  normaliseProfile,
  readOverwhelmed,
  readSensoryProfile,
  writeOverwhelmed,
  writeSensoryProfile,
} from "./storage";

function memoryStorage(): Storage {
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

describe("sensory profile storage", () => {
  it("starts from the documented defaults", () => {
    expect(readSensoryProfile(memoryStorage())).toEqual(DEFAULT_SENSORY_PROFILE);
  });

  it("saves every choice with a version", () => {
    const storage = memoryStorage();
    writeSensoryProfile(
      {
        motion: "none",
        sound: "off",
        density: "simple",
        colours: "high-contrast",
        fontSize: "larger",
      },
      storage,
    );
    const raw = JSON.parse(storage.getItem(SENSORY_STORAGE_KEY) ?? "{}") as Record<string, unknown>;
    expect(raw.version).toBe(SENSORY_PROFILE_VERSION);
    expect(readSensoryProfile(storage)).toEqual({
      motion: "none",
      sound: "off",
      density: "simple",
      colours: "high-contrast",
      fontSize: "larger",
    });
  });

  it("repairs one bad field instead of throwing away the rest", () => {
    const storage = memoryStorage();
    storage.setItem(
      SENSORY_STORAGE_KEY,
      JSON.stringify({ version: 1, motion: "sideways", sound: "off", density: "simple" }),
    );
    expect(readSensoryProfile(storage)).toEqual({
      motion: "normal",
      sound: "off",
      density: "simple",
      colours: "calm",
      fontSize: "normal",
    });
  });

  it("survives corrupt JSON and a blocked store", () => {
    const storage = memoryStorage();
    storage.setItem(SENSORY_STORAGE_KEY, "{not json");
    expect(readSensoryProfile(storage)).toEqual(DEFAULT_SENSORY_PROFILE);
    expect(readSensoryProfile(null)).toEqual(DEFAULT_SENSORY_PROFILE);
    expect(() => {
      writeSensoryProfile(DEFAULT_SENSORY_PROFILE, null);
    }).not.toThrow();
  });

  it("normalises unknown values field by field", () => {
    expect(normaliseProfile(null)).toEqual(DEFAULT_SENSORY_PROFILE);
    expect(normaliseProfile({ motion: "low", sound: 7 })).toEqual({
      ...DEFAULT_SENSORY_PROFILE,
      motion: "low",
    });
  });

  it("never scales text below 100%", () => {
    expect(Object.values(FONT_SCALE).every((value) => value >= 1)).toBe(true);
  });

  it("keeps the overwhelmed flag in session storage semantics", () => {
    const storage = memoryStorage();
    expect(readOverwhelmed(storage)).toBe(false);
    writeOverwhelmed(true, storage);
    expect(readOverwhelmed(storage)).toBe(true);
    writeOverwhelmed(false, storage);
    expect(readOverwhelmed(storage)).toBe(false);
    expect(readOverwhelmed(null)).toBe(false);
  });
});
