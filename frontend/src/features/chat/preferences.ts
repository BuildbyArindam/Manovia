/**
 * Where the "Show how I read your mood" preference lives.
 *
 * Off by default, and deliberately so: an emotion label on a mental-health
 * product is a claim about a person's inner state that the app cannot
 * substantiate. It is opt-in, it is stored locally (never sent anywhere), and
 * the label it shows is the analyser's own word with the confidence left out,
 * because a percentage next to "sadness" would read as a measurement.
 */

import { t } from "@/lib/i18n/i18n";

export const MOOD_HINT_STORAGE_KEY = "manovia.chat.showMoodHint.v1";

export function loadMoodHintPreference(storage: Storage = window.localStorage): boolean {
  try {
    return storage.getItem(MOOD_HINT_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function saveMoodHintPreference(
  enabled: boolean,
  storage: Storage = window.localStorage,
): void {
  try {
    storage.setItem(MOOD_HINT_STORAGE_KEY, enabled ? "true" : "false");
  } catch {
    // Private mode or a blocked origin: the setting simply will not persist.
  }
}

/**
 * The emotions the backend can name, keyed to `chat.moodWords.*` in the
 * locale files: each language keeps its own plain word for the same label.
 */
const EMOTION_WORD_KEYS: readonly string[] = [
  "joy",
  "sadness",
  "anger",
  "fear",
  "surprise",
  "neutral",
  "disgust",
  "contempt",
];

/**
 * The subtle line, or `null` when there is nothing honest to say.
 *
 * A crisis turn has no emotion at all (the pipeline stops before analysis), so
 * this returns `null` there — labelling a safety reply with a mood would be
 * exactly the wrong thing to do.
 */
export function moodHintFor(emotion: string | null | undefined): string | null {
  if (emotion === null || emotion === undefined || emotion === "") {
    return null;
  }
  const normalised = emotion.toLowerCase();
  if (!EMOTION_WORD_KEYS.includes(normalised)) {
    return null;
  }
  return t("chat.moodHint", { word: t(`chat.moodWords.${normalised}`) });
}
