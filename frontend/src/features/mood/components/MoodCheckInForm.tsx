/**
 * The ten-second check-in.
 *
 * One form, in the order a person would actually answer it: how do you feel (one
 * tap), how much energy (one slider), what is here (optional chips), anything to
 * add (optional note), save. Every control is a native form element, so the whole
 * thing is reachable and operable from the keyboard without a single custom key
 * handler — which is the property the end-to-end test exercises with `Tab`,
 * arrows, `Space` and `Enter` only.
 *
 * Three decisions worth stating:
 *
 * * **Valence has no default.** "How do you feel" is the one question the app
 *   must not answer for somebody; the form refuses to save without it and says so
 *   in words, not in red.
 * * **Energy defaults to the middle** and is visibly shown with its word, so a
 *   value is never submitted that the person did not see.
 * * **A note that is too long blocks the save with an explanation** rather than
 *   being truncated: quietly cutting somebody's sentence is worse than asking
 *   them to shorten it.
 */

import { useRef, useState, type FormEvent, type ReactElement } from "react";

import { ApiError } from "@/lib/api";
import type { ApiClient } from "@/lib/api";
import { t } from "@/lib/i18n/i18n";
import { useOnlineStatus } from "@/pwa/useOnlineStatus";

import { MOOD_COPY } from "../copy";
import {
  MAX_EMOTIONS,
  MAX_FACTORS,
  MOOD_EMOTIONS,
  MOOD_FACTORS,
  NOTE_MAX_CHARS,
} from "../taxonomy";
import { useMoodCheckIn, type MoodSubmitResult } from "../useMood";
import { EnergyScale } from "./EnergyScale";
import { FaceScale } from "./FaceScale";
import { TagChips } from "./TagChips";

export interface MoodCheckInFormProps {
  /** Saved on the server, or (Day 25) queued on this device while offline. */
  onSaved: (result: MoodSubmitResult) => void;
  client?: ApiClient;
}

export function MoodCheckInForm({ onSaved, client }: MoodCheckInFormProps): ReactElement {
  const [valence, setValence] = useState<number | null>(null);
  const [energy, setEnergy] = useState(3);
  const [emotions, setEmotions] = useState<string[]>([]);
  const [factors, setFactors] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [showValenceError, setShowValenceError] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const firstFaceRef = useRef<HTMLInputElement | null>(null);
  const mutation = useMoodCheckIn(client);
  const online = useOnlineStatus();

  const noteOverLimit = note.length > NOTE_MAX_CHARS;
  const remaining = NOTE_MAX_CHARS - note.length;

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setErrorMessage(null);

    if (valence === null) {
      setShowValenceError(true);
      firstFaceRef.current?.focus();
      return;
    }
    if (noteOverLimit) {
      return;
    }

    mutation.mutate(
      {
        valence,
        energy,
        emotions,
        factors,
        note: note.trim() === "" ? null : note.trim(),
      },
      {
        onSuccess: (result) => {
          onSaved(result);
        },
        onError: (error) => {
          setErrorMessage(
            error instanceof ApiError && error.code === "consent_required"
              ? MOOD_COPY.errorConsent
              : MOOD_COPY.errorBody,
          );
        },
      },
    );
  }

  return (
    <form
      onSubmit={submit}
      noValidate
      aria-labelledby="mood-form-heading"
      data-testid="mood-check-in-form"
      className="space-y-6 rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <div>
        <h2 id="mood-form-heading" className="text-lg">
          {MOOD_COPY.formHeading}
        </h2>
        <p className="mt-2 text-ink-muted">{MOOD_COPY.formIntro}</p>
      </div>

      <div>
        <FaceScale
          firstInputRef={firstFaceRef}
          value={valence}
          onChange={(value) => {
            setValence(value);
            setShowValenceError(false);
          }}
          legend={MOOD_COPY.valenceLegend}
          hint={MOOD_COPY.valenceHint}
          invalid={showValenceError}
        />
        {showValenceError ? (
          <p role="alert" data-testid="mood-valence-error" className="mt-2 text-sm text-danger">
            {MOOD_COPY.valenceRequired}
          </p>
        ) : null}
      </div>

      <EnergyScale
        value={energy}
        onChange={setEnergy}
        legend={MOOD_COPY.energyLegend}
        hint={MOOD_COPY.energyHint}
      />

      <TagChips
        options={MOOD_EMOTIONS}
        selected={emotions}
        onChange={setEmotions}
        legend={MOOD_COPY.emotionsLegend}
        hint={MOOD_COPY.emotionsHint}
        fullHint={MOOD_COPY.emotionsFull}
        max={MAX_EMOTIONS}
        name="mood-emotions"
      />

      <TagChips
        options={MOOD_FACTORS}
        selected={factors}
        onChange={setFactors}
        legend={MOOD_COPY.factorsLegend}
        hint={MOOD_COPY.factorsHint}
        fullHint={MOOD_COPY.factorsFull}
        max={MAX_FACTORS}
        name="mood-factors"
      />

      <div>
        <label htmlFor="mood-note" className="text-base font-semibold">
          {MOOD_COPY.noteLegend}
        </label>
        <textarea
          id="mood-note"
          name="note"
          rows={3}
          value={note}
          placeholder={MOOD_COPY.notePlaceholder}
          aria-describedby="mood-note-privacy mood-note-count"
          aria-invalid={noteOverLimit || undefined}
          onChange={(event) => {
            setNote(event.target.value);
          }}
          className="mt-3 w-full rounded-xl border border-border bg-bg px-4 py-3 text-base text-ink"
        />
        <p id="mood-note-privacy" className="mt-2 text-sm text-ink-muted">
          {MOOD_COPY.notePrivacy}
        </p>
        <p
          id="mood-note-count"
          data-testid="mood-note-count"
          className={`mt-1 text-sm ${noteOverLimit ? "text-danger" : "text-ink-muted"}`}
        >
          {noteOverLimit ? MOOD_COPY.noteOver : MOOD_COPY.noteRemaining(remaining)}
        </p>
      </div>

      <div>
        <button
          type="submit"
          disabled={mutation.isPending}
          className="inline-flex min-h-12 items-center justify-center rounded-xl bg-accent-bg px-6 text-base font-semibold text-accent-fg disabled:opacity-70"
        >
          {mutation.isPending ? MOOD_COPY.submitting : MOOD_COPY.submit}
        </button>
        <p className="mt-2 text-sm text-ink-muted">{MOOD_COPY.submitHint}</p>
        {online ? null : (
          <p className="mt-2 text-sm text-ink" data-testid="mood-offline-hint">
            {t("mood.offlineHint")}
          </p>
        )}
      </div>

      {errorMessage !== null ? (
        <div role="alert" data-testid="mood-save-error" className="rounded-xl bg-danger-soft p-4">
          <p className="font-semibold">{MOOD_COPY.errorHeading}</p>
          <p className="mt-1 text-sm">{errorMessage}</p>
        </div>
      ) : null}
    </form>
  );
}
