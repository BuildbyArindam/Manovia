/**
 * The breathing cue orb (Day 42).
 *
 * Expands on the inhale and contracts on the exhale at the paced rate. The
 * cue is never the only channel: the phase word ("Inhale" / "Exhale") and the
 * numeric pace are always on screen, and under `prefers-reduced-motion` the
 * orb freezes and the text carries the rhythm (same rule as the exercise
 * guide, Day 18).
 *
 * WELLNESS FEATURE. Not a medical device.
 */

import type { CSSProperties, ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

import type { PacerState } from "../pacer/pacer";

export function PacerOrb({ state }: { state: PacerState }): ReactElement {
  const staticOnly = usePrefersReducedMotion();
  // One cosine over the full breath: small at the start of the inhale, full at
  // the top, back to small at the end of the exhale — continuous everywhere.
  const phaseAngle =
    (state.phase === "inhale" ? state.phaseProgress : 1 + state.phaseProgress) * Math.PI;
  const scale = staticOnly ? 0.85 : 0.55 + 0.45 * (0.5 - 0.5 * Math.cos(phaseAngle));
  const style: CSSProperties = {
    transform: `scale(${scale.toFixed(3)})`,
    transition: staticOnly ? "none" : "transform 240ms linear",
  };
  return (
    <div
      className="flex flex-col items-center gap-3"
      data-testid="pacer-orb"
      data-phase={state.phase}
      data-motion={staticOnly ? "static" : "animated"}
    >
      <p className="text-lg font-semibold" data-testid="pacer-phase">
        {state.phase === "inhale" ? t("biofeedback.pacer.inhale") : t("biofeedback.pacer.exhale")}
      </p>
      <div
        className="relative flex h-56 w-56 max-w-full items-center justify-center rounded-full border border-border-strong bg-surface-muted"
        role="img"
        aria-label={t("biofeedback.pacer.orbLabel", {
          phase: state.phase === "inhale" ? t("biofeedback.pacer.inhale") : t("biofeedback.pacer.exhale"),
          rate: state.rateBpm.toFixed(1),
        })}
      >
        <div
          className="absolute h-40 w-40 rounded-full bg-[var(--manovia-accent-soft)]"
          style={style}
          data-testid="pacer-orb-disc"
          data-static={staticOnly ? "true" : "false"}
        />
        <div className="relative text-center">
          <p className="text-3xl font-semibold tabular-nums" data-testid="pacer-rate">
            {state.rateBpm.toFixed(1)}
          </p>
          <p className="text-sm text-ink-muted">{t("biofeedback.pacer.perMinute")}</p>
        </div>
      </div>
    </div>
  );
}
