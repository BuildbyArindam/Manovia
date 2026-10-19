/**
 * The optional offer of the simpler view (Day 46).
 *
 * Shown only after several activated-and-unpleasant check-ins, never on its
 * own, and dismissed for a month with one tap. "Try the simpler view" turns
 * overwhelmed mode on now; it does not change the stored sensory profile.
 *
 * The copy is deliberately non-diagnostic: it describes what the app noticed
 * ("a few heavy, restless check-ins") and offers a display change. It does not
 * name an emotion, imply a condition, or promise that the view will help.
 */

import { useState, type ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";

import { dismissSuggestion, shouldSuggestSimplifiedMode } from "./arousal";
import { useSensory } from "./SensoryProvider";

export function SensorySuggestion(): ReactElement | null {
  const { overwhelmed, setOverwhelmed } = useSensory();
  const [offered, setOffered] = useState<boolean>(() =>
    typeof window === "undefined" ? false : shouldSuggestSimplifiedMode(),
  );

  if (!offered || overwhelmed) {
    return null;
  }

  function dismiss(): void {
    dismissSuggestion();
    setOffered(false);
  }

  return (
    <aside
      aria-labelledby="sensory-suggestion-heading"
      data-testid="sensory-suggestion"
      className="rounded-2xl border border-border bg-surface-muted p-4"
    >
      <h2 id="sensory-suggestion-heading" className="text-base">
        {t("sensory.suggestion.heading")}
      </h2>
      <p className="mt-1 text-sm text-ink-muted">{t("sensory.suggestion.body")}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => {
            setOverwhelmed(true);
            setOffered(false);
          }}
          className="rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg"
        >
          {t("sensory.suggestion.try")}
        </button>
        <button
          type="button"
          onClick={dismiss}
          className="rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
        >
          {t("sensory.suggestion.notNow")}
        </button>
      </div>
    </aside>
  );
}
