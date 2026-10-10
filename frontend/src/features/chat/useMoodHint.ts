/**
 * The "Show how I read your mood" preference, shared by the chat and Settings.
 *
 * Two listeners because the setting can change in two places: the `storage`
 * event covers another tab, and a custom event covers this tab (React state in
 * a different component tree does not re-read `localStorage` on its own).
 */

import { useCallback, useEffect, useState } from "react";

import { loadMoodHintPreference, saveMoodHintPreference } from "./preferences";

export const MOOD_HINT_EVENT = "manovia:mood-hint";

export function useMoodHintPreference(): [boolean, (enabled: boolean) => void] {
  const [enabled, setEnabled] = useState<boolean>(() => loadMoodHintPreference());

  useEffect(() => {
    const sync = (): void => {
      setEnabled(loadMoodHintPreference());
    };
    window.addEventListener(MOOD_HINT_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(MOOD_HINT_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const set = useCallback((next: boolean): void => {
    saveMoodHintPreference(next);
    setEnabled(next);
    window.dispatchEvent(new Event(MOOD_HINT_EVENT));
  }, []);

  return [enabled, set];
}
