import type { LibraryEntry, MeditationScript } from "./types";

/**
 * The meditation library, entirely local.
 *
 * The server stores nothing about meditations (ADR 0045); this is the
 * "save favourites locally" half of the feature. Scripts are identified by
 * `content_hash` — re-saving the same composition replaces the entry, so
 * the library can never grow with duplicates.
 */

const STORAGE_KEY = "manovia.meditations.library.v1";
/** Keep the last N generations on hand for "play again" history. */
export const MAX_HISTORY = 10;

export interface LibraryState {
  entries: LibraryEntry[];
  /** Most recent first; content hashes of scripts composed on this device. */
  history: string[];
}

export function emptyLibrary(): LibraryState {
  return { entries: [], history: [] };
}

export function readLibrary(storage: Storage = globalThis.localStorage): LibraryState {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (raw === null) {
      return emptyLibrary();
    }
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      !Array.isArray((parsed as { entries?: unknown }).entries) ||
      !Array.isArray((parsed as { history?: unknown }).history)
    ) {
      return emptyLibrary();
    }
    return parsed as LibraryState;
  } catch {
    // Corrupt or blocked storage: start empty rather than crash the page.
    return emptyLibrary();
  }
}

export function writeLibrary(
  state: LibraryState,
  storage: Storage = globalThis.localStorage,
): void {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage full or blocked (private mode): favourites are a convenience,
    // not load-bearing. The session keeps working in memory.
  }
}

/** Save (or re-save) a favourite. Same content_hash replaces the entry. */
export function saveFavourite(
  state: LibraryState,
  script: MeditationScript,
  label: string,
  paletteId: string,
  now: () => Date = () => new Date(),
): LibraryState {
  const entry: LibraryEntry = {
    saved_at: now().toISOString(),
    label,
    palette_id: paletteId,
    script,
  };
  const entries = [entry, ...state.entries.filter((e) => e.script.content_hash !== script.content_hash)];
  return pushHistory({ entries, history: state.history }, script.content_hash);
}

export function removeFavourite(state: LibraryState, contentHash: string): LibraryState {
  return {
    ...state,
    entries: state.entries.filter((entry) => entry.script.content_hash !== contentHash),
  };
}

export function getFavourite(state: LibraryState, contentHash: string): LibraryEntry | undefined {
  return state.entries.find((entry) => entry.script.content_hash === contentHash);
}

/** Record that a script was composed here (for "play again"). */
export function pushHistory(state: LibraryState, contentHash: string): LibraryState {
  const history = [contentHash, ...state.history.filter((hash) => hash !== contentHash)].slice(
    0,
    MAX_HISTORY,
  );
  return { ...state, history };
}
