import type { CSSProperties, ReactElement } from "react";

import { CUE_LABELS } from "./copy";
import type { ExerciseStep, StepCue } from "./types";
import { t } from "@/lib/i18n/i18n";

// Literal classes are essential: Tailwind purges dynamically concatenated
// names from @layer components, leaving a perfectly valid SVG that never moves.
export const ORB_CLASSES: Record<StepCue, string> = {
  instruction: "exercise-orb-instruction",
  inhale: "exercise-orb-inhale",
  "hold-in": "exercise-orb-hold-in",
  exhale: "exercise-orb-exhale",
  "hold-out": "exercise-orb-hold-out",
};

interface BreathingGuideProps {
  step: ExerciseStep;
  secondsLeft: number;
  paused: boolean;
  staticOnly: boolean;
}

/** The orb is decoration. Visible instructions and the static timer carry all information. */
export function BreathingGuide({
  step,
  secondsLeft,
  paused,
  staticOnly,
}: BreathingGuideProps): ReactElement {
  const style: CSSProperties = {
    animationDuration: `${step.duration_seconds}s`,
    animationPlayState: paused ? "paused" : "running",
  };
  return (
    <div
      className="space-y-3 text-center"
      data-testid="breathing-guide"
      data-motion={staticOnly ? "static-countdown" : "animated"}
    >
      <p className="text-lg font-semibold">{CUE_LABELS[step.cue]}</p>
      <div
        className="relative mx-auto flex h-60 w-60 max-w-full items-center justify-center rounded-full border border-border-strong bg-surface-muted"
        data-testid={staticOnly ? "static-countdown" : "animated-breathing-guide"}
      >
        {staticOnly ? null : (
          <svg
            key={step.id}
            viewBox="0 0 200 200"
            aria-hidden="true"
            focusable="false"
            className={`exercise-orb ${ORB_CLASSES[step.cue]} absolute inset-0 h-full w-full`}
            style={style}
          >
            <circle
              cx="100"
              cy="100"
              r="78"
              fill="var(--manovia-accent-soft)"
              stroke="var(--manovia-accent-text)"
              strokeWidth="2"
            />
          </svg>
        )}
        <div className="relative rounded-full bg-surface px-4 py-2">
          <span
            role="timer"
            aria-live="off"
            aria-label={t("exercises.timeRemainingLabel")}
            className="block"
          >
            <span
              className="block text-4xl font-semibold tabular-nums"
              data-testid="step-countdown"
            >
              {secondsLeft}
            </span>
            <span className="text-sm text-ink-muted">{t("exercises.secondsLeft")}</span>
          </span>
        </div>
      </div>
    </div>
  );
}
