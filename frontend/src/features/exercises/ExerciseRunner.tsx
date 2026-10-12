import { useEffect, useRef, useState, type ReactElement } from "react";
import { t } from "@/lib/i18n/i18n";

import { FaceScale } from "@/features/mood/components/FaceScale";
import { ApiError, type ApiClient } from "@/lib/api";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

import { BreathingGuide } from "./BreathingGuide";
import { EXERCISE_COPY as COPY, formatDuration } from "./copy";
import { completeExercise } from "./endpoints";
import type { CompletionPayload, ExerciseCompletion, ExerciseDefinition } from "./types";
import { useExerciseTimer } from "./useExerciseTimer";

export interface ExerciseRunnerProps {
  exercise: ExerciseDefinition;
  client?: ApiClient;
  onClose: () => void;
  onSaved?: (completion: ExerciseCompletion) => void;
}

export function ExerciseRunner({
  exercise,
  client,
  onClose,
  onSaved,
}: ExerciseRunnerProps): ReactElement {
  const timer = useExerciseTimer(exercise.steps);
  const { state } = timer;
  const step = exercise.steps[state.stepIndex];
  const reducedMotion = usePrefersReducedMotion();
  const [textOnly, setTextOnly] = useState(false);
  const [haptics, setHaptics] = useState(false);
  const canVibrate = typeof navigator.vibrate === "function";
  const [preRating, setPreRating] = useState<number | null>(null);
  const [postRating, setPostRating] = useState<number | null>(null);
  const [choice, setChoice] = useState<string | null>(null);
  const [completionId] = useState(() => crypto.randomUUID());
  const [payload, setPayload] = useState<CompletionPayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<ExerciseCompletion | null>(null);
  const [saveFailure, setSaveFailure] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const finishedHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);

  useEffect(() => {
    if (state.status === "finished") {
      finishedHeading.current?.focus();
    }
  }, [state.status]);

  useEffect(() => {
    if (!haptics || !canVibrate || state.status !== "running") {
      return;
    }
    try {
      navigator.vibrate(30);
    } catch {
      // Permission/policy/hardware failure must not interrupt an exercise.
    }
    return () => {
      try {
        navigator.vibrate(0);
      } catch {
        // A browser can expose the API but disallow calls.
      }
    };
  }, [canVibrate, haptics, state.stepIndex, state.status]);

  const save = async (): Promise<void> => {
    if (saving || saved !== null) {
      return;
    }
    // Freeze the first attempted payload. A retry after a lost response must
    // not change ratings under the same idempotency key.
    const attempt = payload ?? {
      completion_id: completionId,
      pre_rating: preRating,
      post_rating: postRating,
    };
    setPayload(attempt);
    setSaving(true);
    setSaveFailure(null);
    try {
      const result = await completeExercise(exercise.id, attempt, client);
      setSaved(result);
      onSaved?.(result);
    } catch (error) {
      setSaveFailure(error instanceof ApiError ? error.message : COPY.unsaved);
    } finally {
      setSaving(false);
    }
  };

  const ready = state.status === "ready";
  const finished = state.status === "finished";
  const paused = state.status === "paused";
  const pauseText = state.pauseReason === "manual" ? COPY.paused : COPY.backgroundPause;
  const announcement = ready
    ? "Ready. Read the notes before starting."
    : finished
      ? COPY.finished
      : paused
        ? pauseText
        : `Step ${state.stepIndex + 1} of ${exercise.steps.length}. ${step?.instruction ?? ""}`;

  return (
    <section
      className="space-y-5 rounded-2xl border border-border bg-surface p-5 shadow-soft sm:p-8"
      aria-labelledby="exercise-title"
      data-testid="exercise-runner"
    >
      <div>
        <p className="text-sm text-ink-muted">
          Guided exercise · {formatDuration(exercise.duration_seconds)}
        </p>
        <h2 id="exercise-title" ref={heading} tabIndex={-1} className="mt-1 text-2xl">
          {exercise.title.en}
        </h2>
        <p className="mt-2 text-ink-muted">{exercise.description}</p>
      </div>
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
        data-testid="step-announcement"
      >
        {announcement}
      </div>

      <aside
        className="rounded-xl border border-border-strong bg-surface-muted p-4"
        aria-label={t("exercises.safetyNotesLabel")}
      >
        <h3 className="font-semibold">{COPY.beforeBegin}</h3>
        <ul className="mt-2 list-disc space-y-2 pl-5 text-sm">
          {exercise.contraindications.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </aside>

      <details className="rounded-xl border border-border p-4">
        <summary className="cursor-pointer font-semibold">{COPY.evidence}</summary>
        <ul className="mt-2 list-disc space-y-2 pl-5 text-sm">
          {exercise.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
        <p className="mt-3 text-sm text-ink-muted">{exercise.evidence_note}</p>
      </details>

      {!finished ? (
        <div className="space-y-3 text-sm">
          <label className="flex items-start gap-3">
            <input
              type="checkbox"
              checked={reducedMotion || textOnly}
              disabled={reducedMotion}
              onChange={(event) => setTextOnly(event.target.checked)}
              className="mt-1"
            />
            <span>{COPY.textOnly}</span>
          </label>
          {reducedMotion ? <p>{COPY.reducedMotion}</p> : null}
          {canVibrate ? (
            <>
              <label className="flex items-start gap-3">
                <input
                  type="checkbox"
                  checked={haptics}
                  onChange={(event) => setHaptics(event.target.checked)}
                  className="mt-1"
                />
                <span>{COPY.haptics}</span>
              </label>
              <p className="text-ink-muted">{COPY.hapticsNote}</p>
            </>
          ) : (
            <p className="text-ink-muted">{COPY.hapticsUnavailable}</p>
          )}
        </div>
      ) : null}

      {ready ? (
        <div className="space-y-4">
          <FaceScale
            value={preRating}
            onChange={setPreRating}
            name="exercise-pre-rating"
            legend={COPY.before}
            hint={COPY.ratingHint}
          />
          {preRating !== null ? (
            <button type="button" className="btn-secondary" onClick={() => setPreRating(null)}>
              {COPY.clearRating}
            </button>
          ) : null}
          <div className="flex flex-wrap gap-3">
            <button type="button" className="btn-primary" onClick={timer.start}>
              {COPY.start}
            </button>
            <button type="button" className="btn-secondary" onClick={onClose}>
              {COPY.back}
            </button>
          </div>
        </div>
      ) : finished ? (
        <div className="space-y-4" data-testid="exercise-finished">
          <h3 ref={finishedHeading} tabIndex={-1} className="text-xl">
            {COPY.finished}
          </h3>
          <p className="text-ink-muted">{COPY.finishedNote}</p>
          {saved === null ? (
            <>
              <fieldset disabled={payload !== null} className="border-0 p-0">
                <legend className="sr-only">{t("exercises.optionalAfterRating")}</legend>
                <FaceScale
                  value={postRating}
                  onChange={setPostRating}
                  name="exercise-post-rating"
                  legend={COPY.after}
                  hint={COPY.ratingHint}
                />
                {postRating !== null ? (
                  <button
                    type="button"
                    className="btn-secondary mt-3"
                    onClick={() => setPostRating(null)}
                  >
                    {COPY.clearRating}
                  </button>
                ) : null}
              </fieldset>
              <p className="text-sm text-ink-muted">{COPY.saveNote}</p>
              {saveFailure !== null ? (
                <div
                  role="alert"
                  className="rounded-xl border border-danger bg-danger-soft p-4"
                  data-testid="completion-unsaved"
                >
                  <p>{COPY.unsaved}</p>
                  {saveFailure !== COPY.unsaved ? <p className="mt-2">{saveFailure}</p> : null}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  className="btn-primary"
                  disabled={saving}
                  onClick={() => {
                    void save();
                  }}
                >
                  {saving ? COPY.saving : saveFailure !== null ? COPY.retrySave : COPY.save}
                </button>
                <button type="button" className="btn-secondary" disabled={saving} onClick={onClose}>
                  {saveFailure !== null ? COPY.discardFailed : COPY.discard}
                </button>
              </div>
            </>
          ) : (
            <div className="space-y-3" role="status" data-testid="completion-saved">
              <p className="font-semibold">{COPY.saved}</p>
              <p>{saved.summary}</p>
              <button type="button" className="btn-secondary" onClick={onClose}>
                {COPY.back}
              </button>
            </div>
          )}
        </div>
      ) : step !== undefined ? (
        <div className="space-y-5">
          <p className="text-sm text-ink-muted">
            Step {state.stepIndex + 1} of {exercise.steps.length}
          </p>
          <p className="text-lg font-medium" data-testid="exercise-instruction">
            {step.instruction}
          </p>
          <BreathingGuide
            step={step}
            secondsLeft={timer.secondsLeft}
            paused={paused}
            staticOnly={reducedMotion || textOnly || !exercise.tags.includes("breathing")}
          />
          {step.choices.length > 0 ? (
            <fieldset className="space-y-2">
              <legend className="font-semibold">{COPY.chooseAction}</legend>
              {step.choices.map((option) => (
                <label
                  key={option.id}
                  className="flex items-start gap-3 rounded-xl border border-border p-3"
                >
                  <input
                    type="radio"
                    name={`choice-${step.id}`}
                    checked={choice === option.label}
                    onChange={() => setChoice(option.label)}
                    className="mt-1"
                  />
                  <span>{option.label}</span>
                </label>
              ))}
            </fieldset>
          ) : null}
          {choice !== null ? (
            <p className="rounded-xl bg-accent-soft p-3" data-testid="selected-action">
              Your small action: {choice}
            </p>
          ) : null}
          <progress
            className="h-3 w-full accent-accent"
            max={exercise.duration_seconds * 1000}
            value={state.elapsedMs}
            aria-label={t("exercises.progressLabel")}
          />
          {paused ? <p className="rounded-xl bg-surface-muted p-3">{pauseText}</p> : null}
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              className="btn-primary"
              onClick={() => {
                if (paused) {
                  timer.resume();
                } else {
                  timer.pause();
                }
              }}
            >
              {paused ? COPY.resume : COPY.pause}
            </button>
            <button type="button" className="btn-secondary" onClick={onClose}>
              {COPY.stop}
            </button>
          </div>
          <p className="text-sm text-ink-muted">{COPY.stopNote}</p>
        </div>
      ) : null}
    </section>
  );
}
