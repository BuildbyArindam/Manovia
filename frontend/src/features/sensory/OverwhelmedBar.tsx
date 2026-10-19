/**
 * "I'm overwhelmed" (Day 46).
 *
 * One tap replaces the navigation with four destinations — breathe, ground,
 * talk, help — and hides charts, badges and secondary copy everywhere else.
 * The current page stays where it is: yanking somebody out of a half-written
 * chat because they said they were overwhelmed would be a second unkind thing
 * happening at once.
 *
 * Why a bar and not a modal: a dialog steals focus and asks for a decision.
 * The bar is present, quiet, and has one obvious way out. Its exit keeps the
 * focus on the same control so keyboard users do not lose their place.
 */

import { useState, type ReactElement } from "react";
import { Link } from "react-router-dom";

import { CrisisResourcesModal } from "@/components/CrisisResourcesModal";
import { t } from "@/lib/i18n/i18n";

import { useSensory } from "./SensoryProvider";

interface CalmAction {
  to: string;
  labelKey: string;
  hintKey: string;
}

const ACTIONS: readonly CalmAction[] = [
  {
    to: "/calm",
    labelKey: "sensory.overwhelmed.breathe",
    hintKey: "sensory.overwhelmed.breatheHint",
  },
  {
    to: "/exercises",
    labelKey: "sensory.overwhelmed.ground",
    hintKey: "sensory.overwhelmed.groundHint",
  },
  { to: "/chat", labelKey: "sensory.overwhelmed.talk", hintKey: "sensory.overwhelmed.talkHint" },
];

export function OverwhelmedBar(): ReactElement | null {
  const { setOverwhelmed, overwhelmed } = useSensory();
  const [helpOpen, setHelpOpen] = useState(false);
  if (!overwhelmed) {
    return null;
  }

  return (
    <section
      aria-labelledby="overwhelmed-heading"
      data-testid="overwhelmed-bar"
      className="mx-auto mb-4 w-full max-w-6xl rounded-2xl border border-border-strong bg-surface p-4 shadow-soft"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="overwhelmed-heading" className="text-lg">
            {t("sensory.overwhelmed.heading")}
          </h2>
          <p className="mt-1 text-sm text-ink-muted">{t("sensory.overwhelmed.body")}</p>
        </div>
        <button
          type="button"
          data-testid="overwhelmed-exit"
          onClick={() => {
            setOverwhelmed(false);
          }}
          className="rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
        >
          {t("sensory.overwhelmed.exit")}
        </button>
      </div>

      <ul className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {ACTIONS.map((action) => (
          <li key={action.to}>
            <Link
              to={action.to}
              className="flex min-h-11 flex-col justify-center rounded-xl border border-border-strong px-3 py-2 font-semibold text-ink"
            >
              {t(action.labelKey)}
              <span className="text-xs font-normal text-ink-muted">{t(action.hintKey)}</span>
            </Link>
          </li>
        ))}
        <li>
          <button
            type="button"
            onClick={() => {
              setHelpOpen(true);
            }}
            aria-haspopup="dialog"
            data-testid="overwhelmed-help"
            className="flex min-h-11 w-full flex-col justify-center rounded-xl border-2 border-danger bg-danger-soft px-3 py-2 text-left font-semibold text-danger"
          >
            {t("sensory.overwhelmed.help")}
            <span className="text-xs font-normal">{t("sensory.overwhelmed.helpHint")}</span>
          </button>
        </li>
      </ul>

      {helpOpen ? (
        <CrisisResourcesModal
          onClose={() => {
            setHelpOpen(false);
          }}
        />
      ) : null}
    </section>
  );
}
