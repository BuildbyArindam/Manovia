/**
 * The one-tap "I'm overwhelmed" control, in the header on every page.
 *
 * It is a toggle rather than a one-way door, and it says what it will do. On a
 * phone the label shortens to fit beside "Need help now?" without shrinking the
 * target below 44px.
 */

import type { ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";

import { useSensory } from "./SensoryProvider";

export function OverwhelmedButton(): ReactElement {
  const { overwhelmed, setOverwhelmed } = useSensory();
  return (
    <button
      type="button"
      aria-pressed={overwhelmed}
      data-testid="overwhelmed-toggle"
      onClick={() => {
        setOverwhelmed(!overwhelmed);
      }}
      className={[
        "inline-flex min-h-11 items-center rounded-xl border px-2 py-1.5 text-xs font-semibold sm:px-3 sm:text-sm",
        overwhelmed
          ? "border-accent-text bg-accent-soft text-ink"
          : "border-border-strong text-ink",
      ].join(" ")}
    >
      <span className="hidden sm:inline">
        {overwhelmed ? t("sensory.overwhelmed.toggleOn") : t("sensory.overwhelmed.toggleOff")}
      </span>
      <span className="sm:hidden">
        {overwhelmed
          ? t("sensory.overwhelmed.toggleOnShort")
          : t("sensory.overwhelmed.toggleOffShort")}
      </span>
    </button>
  );
}
