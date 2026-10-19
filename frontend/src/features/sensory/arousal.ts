/**
 * The optional "would a simpler view help?" suggestion (Day 46).
 *
 * The brief is explicit that this is a suggestion and never forced. Two design
 * choices follow from that, and both are load-bearing:
 *
 * * The app never switches modes by itself. It only *offers* the switch, and
 *   the offer is dismissible.
 * * Only timestamps are stored locally — no emotion labels, no free text, no
 *   identifiers. Three check-ins with high energy and low mood inside two weeks
 *   is the whole signal; there is nothing else to leak.
 *
 * A high-arousal check-in is operationalised as energy >= 4 and valence <= 2
 * on the app's own five-point scales: activated and unpleasant. That is a
 * heuristic for "this might be a lot right now", not a clinical measure, and it
 * is deliberately conservative (both conditions must hold).
 */

export const HIGH_AROUSAL_MIN_ENERGY = 4;
export const HIGH_AROUSAL_MAX_VALENCE = 2;
export const HIGH_AROUSAL_THRESHOLD = 3;
export const HIGH_AROUSAL_WINDOW_MS = 14 * 24 * 60 * 60 * 1000;
/** A dismissed offer stays dismissed for a month; it is never nagging. */
export const SUGGESTION_DISMISS_MS = 30 * 24 * 60 * 60 * 1000;
export const AROUSAL_STORAGE_KEY = "manovia.arousal-events.v1";
export const SUGGESTION_DISMISSED_KEY = "manovia.arousal-suggestion-dismissed.v1";
const MAX_EVENTS = 50;

export interface ArousalCheckIn {
  valence: number;
  energy: number;
}

/** Activated and unpleasant, on the five-point scales the check-in uses. */
export function isHighArousal({ valence, energy }: ArousalCheckIn): boolean {
  return energy >= HIGH_AROUSAL_MIN_ENERGY && valence <= HIGH_AROUSAL_MAX_VALENCE;
}

function safeLocalStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function readEvents(storage: Storage | null): number[] {
  if (storage === null) {
    return [];
  }
  try {
    const raw = storage.getItem(AROUSAL_STORAGE_KEY);
    if (raw === null) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter((value): value is number => typeof value === "number" && value > 0);
  } catch {
    return [];
  }
}

function writeEvents(events: number[], storage: Storage | null): void {
  if (storage === null) {
    return;
  }
  try {
    storage.setItem(AROUSAL_STORAGE_KEY, JSON.stringify(events.slice(-MAX_EVENTS)));
  } catch {
    // A blocked store only means the offer cannot be made. Nothing breaks.
  }
}

/** Keep only events inside the window ending at `now`. */
export function recentEvents(events: readonly number[], now: number): number[] {
  return events.filter((at) => now - at >= 0 && now - at <= HIGH_AROUSAL_WINDOW_MS);
}

/**
 * Record a saved check-in if it is high-arousal. Returns the recent count, so
 * a caller can decide whether to offer the suggestion without reading storage
 * twice.
 */
export function recordCheckIn(
  checkIn: ArousalCheckIn,
  now: number = Date.now(),
  storage: Storage | null = safeLocalStorage(),
): number {
  const events = readEvents(storage);
  if (isHighArousal(checkIn)) {
    events.push(now);
    writeEvents(events, storage);
  }
  return recentEvents(events, now).length;
}

export function highArousalCount(
  now: number = Date.now(),
  storage: Storage | null = safeLocalStorage(),
): number {
  return recentEvents(readEvents(storage), now).length;
}

export function suggestionDismissed(
  now: number = Date.now(),
  storage: Storage | null = safeLocalStorage(),
): boolean {
  if (storage === null) {
    return false;
  }
  try {
    const raw = storage.getItem(SUGGESTION_DISMISSED_KEY);
    if (raw === null) {
      return false;
    }
    const at = Number(raw);
    return Number.isFinite(at) && now - at >= 0 && now - at < SUGGESTION_DISMISS_MS;
  } catch {
    return false;
  }
}

/** True when the app may show the offer. It never acts on this by itself. */
export function shouldSuggestSimplifiedMode(
  now: number = Date.now(),
  storage: Storage | null = safeLocalStorage(),
): boolean {
  if (suggestionDismissed(now, storage)) {
    return false;
  }
  return highArousalCount(now, storage) >= HIGH_AROUSAL_THRESHOLD;
}

export function dismissSuggestion(
  now: number = Date.now(),
  storage: Storage | null = safeLocalStorage(),
): void {
  if (storage === null) {
    return;
  }
  try {
    storage.setItem(SUGGESTION_DISMISSED_KEY, String(now));
  } catch {
    // Nothing to do; the banner can simply be hidden for this render.
  }
}
