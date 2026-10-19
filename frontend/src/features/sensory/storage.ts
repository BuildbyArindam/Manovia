/**
 * Reading and writing the sensory profile.
 *
 * Everything here fails soft. A blocked `localStorage` (Safari private mode, a
 * corporate policy, a reader who turned site data off) must leave the
 * interface at its defaults rather than break the page — the same rule the
 * theme provider follows. A corrupt or older stored object is repaired field by
 * field, so one bad key never throws away the rest of a person's choices.
 */

import {
  DEFAULT_SENSORY_PROFILE,
  OVERWHELMED_STORAGE_KEY,
  SENSORY_PROFILE_VERSION,
  SENSORY_STORAGE_KEY,
  isColourMode,
  isDensityLevel,
  isFontSize,
  isMotionLevel,
  isSoundLevel,
  type SensoryProfile,
} from "./types";

/** Repair an unknown stored value into a complete, valid profile. */
export function normaliseProfile(raw: unknown): SensoryProfile {
  if (raw === null || typeof raw !== "object") {
    return { ...DEFAULT_SENSORY_PROFILE };
  }
  const candidate = raw as Record<string, unknown>;
  return {
    motion: isMotionLevel(candidate.motion) ? candidate.motion : DEFAULT_SENSORY_PROFILE.motion,
    sound: isSoundLevel(candidate.sound) ? candidate.sound : DEFAULT_SENSORY_PROFILE.sound,
    density: isDensityLevel(candidate.density)
      ? candidate.density
      : DEFAULT_SENSORY_PROFILE.density,
    colours: isColourMode(candidate.colours) ? candidate.colours : DEFAULT_SENSORY_PROFILE.colours,
    fontSize: isFontSize(candidate.fontSize)
      ? candidate.fontSize
      : DEFAULT_SENSORY_PROFILE.fontSize,
  };
}

export function readSensoryProfile(storage: Storage | null = safeStorage("local")): SensoryProfile {
  if (storage === null) {
    return { ...DEFAULT_SENSORY_PROFILE };
  }
  try {
    const raw = storage.getItem(SENSORY_STORAGE_KEY);
    if (raw === null) {
      return { ...DEFAULT_SENSORY_PROFILE };
    }
    const parsed: unknown = JSON.parse(raw);
    if (
      parsed !== null &&
      typeof parsed === "object" &&
      (parsed as { version?: unknown }).version !== SENSORY_PROFILE_VERSION
    ) {
      // Unknown version: keep whatever fields still make sense. The repair above
      // is exactly the migration, and a future version can extend it.
      return normaliseProfile(parsed);
    }
    return normaliseProfile(parsed);
  } catch {
    return { ...DEFAULT_SENSORY_PROFILE };
  }
}

export function writeSensoryProfile(
  profile: SensoryProfile,
  storage: Storage | null = safeStorage("local"),
): void {
  if (storage === null) {
    return;
  }
  try {
    storage.setItem(
      SENSORY_STORAGE_KEY,
      JSON.stringify({ version: SENSORY_PROFILE_VERSION, ...profile }),
    );
  } catch {
    // A full or blocked store changes nothing about this session.
  }
}

export function readOverwhelmed(storage: Storage | null = safeStorage("session")): boolean {
  if (storage === null) {
    return false;
  }
  try {
    return storage.getItem(OVERWHELMED_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeOverwhelmed(
  active: boolean,
  storage: Storage | null = safeStorage("session"),
): void {
  if (storage === null) {
    return;
  }
  try {
    if (active) {
      storage.setItem(OVERWHELMED_STORAGE_KEY, "1");
    } else {
      storage.removeItem(OVERWHELMED_STORAGE_KEY);
    }
  } catch {
    // Same rule: a blocked store must not stop the switch working now.
  }
}

/** `localStorage` or `sessionStorage`, or null where the browser refuses it. */
export function safeStorage(kind: "local" | "session"): Storage | null {
  try {
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}
