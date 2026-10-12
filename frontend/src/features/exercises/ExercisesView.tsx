import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";
import type { ApiClient } from "@/lib/api";

import { EXERCISE_COPY as COPY, emotionOptions, formatDuration } from "./copy";
import { t } from "@/lib/i18n/i18n";
import { EXERCISE_HISTORY_KEY, fetchExercises, fetchRecommendations } from "./endpoints";
import { ExerciseHistory } from "./ExerciseHistory";
import { ExerciseRunner } from "./ExerciseRunner";
import type { ExerciseDefinition, ExerciseEmotion, Intensity } from "./types";

export function ExercisesView({ client }: { client?: ApiClient }): ReactElement {
  const queryClient = useQueryClient();
  const [active, setActive] = useState<ExerciseDefinition | null>(null);
  const [emotion, setEmotion] = useState<ExerciseEmotion | "">("");
  const [intensity, setIntensity] = useState<Intensity>("moderate");
  const [historyOffset, setHistoryOffset] = useState(0);
  const returnFocus = useRef<string | null>(null);
  const launchButtons = useRef(new Map<string, HTMLButtonElement>());
  const catalog = useQuery({
    queryKey: ["exercises", "catalog"],
    queryFn: () => fetchExercises(client),
    staleTime: Infinity,
    retry: false,
  });
  const recommendations = useQuery({
    queryKey: ["exercises", "recommendations", emotion, intensity],
    queryFn: () => fetchRecommendations(emotion as ExerciseEmotion, intensity, client),
    enabled: emotion !== "",
    staleTime: Infinity,
    retry: false,
  });
  const why = new Map(
    recommendations.data?.recommendations.map((item) => [item.exercise_id, item.why]) ?? [],
  );
  const exercises = [...(catalog.data?.exercises ?? [])].sort(
    (a, b) => Number(why.has(b.id)) - Number(why.has(a.id)),
  );

  const close = (): void => {
    setActive(null);
    // The launch button is mounted again on the following paint.
    requestAnimationFrame(() => {
      if (returnFocus.current !== null) {
        launchButtons.current.get(returnFocus.current)?.focus();
      }
    });
  };

  return (
    <div className="space-y-6" data-testid="exercises-page">
      <PageHeader title={COPY.heading} lede={COPY.lede} />
      <div className="space-y-2 rounded-2xl border border-border bg-surface p-5 text-sm text-ink-muted">
        <p>{COPY.framing}</p>
        <p>{COPY.offline}</p>
      </div>

      {active !== null ? (
        <ExerciseRunner
          key={active.id}
          exercise={active}
          client={client}
          onClose={close}
          onSaved={() => {
            setHistoryOffset(0);
            void queryClient.invalidateQueries({ queryKey: EXERCISE_HISTORY_KEY });
          }}
        />
      ) : (
        <>
          <section
            className="space-y-3 rounded-2xl border border-border bg-surface p-5"
            aria-labelledby="exercise-suggestions"
          >
            <h2 id="exercise-suggestions" className="text-xl">
              {COPY.suggestions}
            </h2>
            <p className="text-sm text-ink-muted">{COPY.suggestionsNote}</p>
            <div className="flex flex-wrap gap-4">
              <label className="flex flex-col gap-1">
                <span>{t("exercises.feelingQuestion")}</span>
                <select
                  className="min-h-11 rounded-lg border border-border-strong bg-surface px-3 py-2"
                  value={emotion}
                  onChange={(event) => setEmotion(event.target.value as ExerciseEmotion | "")}
                >
                  <option value="">{t("exercises.browseWithout")}</option>
                  {emotionOptions().map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span>{t("exercises.intensityQuestion")}</span>
                <select
                  className="min-h-11 rounded-lg border border-border-strong bg-surface px-3 py-2"
                  value={intensity}
                  onChange={(event) => setIntensity(event.target.value as Intensity)}
                  disabled={emotion === ""}
                >
                  <option value="low">{t("exercises.intensityLow")}</option>
                  <option value="moderate">{t("exercises.intensityModerate")}</option>
                  <option value="high">{t("exercises.intensityHigh")}</option>
                </select>
              </label>
            </div>
            {emotion !== "" && recommendations.isFetching ? (
              <p role="status">{t("exercises.loadingSuggestions")}</p>
            ) : null}
            {emotion !== "" && recommendations.isError ? (
              <p>
                {t("exercises.suggestionsUnavailable")}
              </p>
            ) : null}
          </section>

          {catalog.isPending ? <p role="status">{COPY.loading}</p> : null}
          {catalog.isError ? (
            <div role="alert">
              <p>{COPY.unavailable}</p>
              <button
                type="button"
                className="btn-secondary mt-2"
                onClick={() => {
                  void catalog.refetch();
                }}
              >
                {t("exercises.retryCatalog")}
              </button>
            </div>
          ) : null}
          <div
            role="group"
            className="grid gap-4 lg:grid-cols-2"
            aria-label={t("exercises.choicesLabel")}
          >
            {exercises.map((exercise) => (
              <article
                key={exercise.id}
                className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-5 shadow-soft"
                data-testid={`exercise-card-${exercise.id}`}
              >
                {why.has(exercise.id) ? (
                  <p className="text-sm font-semibold text-accent">{t("exercises.suggestedHeading")}</p>
                ) : null}
                <h2 className="text-xl">{exercise.title.en}</h2>
                <p className="text-sm text-ink-muted">
                  {formatDuration(exercise.duration_seconds)} · {t("exercises.noAudio")}
                </p>
                <p className="flex-1 text-ink-muted">{exercise.description}</p>
                {why.has(exercise.id) ? (
                  <details className="rounded-xl bg-surface-muted p-3">
                    <summary className="cursor-pointer font-semibold">{COPY.why}</summary>
                    <p className="mt-2 text-sm">{why.get(exercise.id)}</p>
                  </details>
                ) : null}
                <button
                  type="button"
                  className="btn-secondary"
                  ref={(element) => {
                    if (element === null) {
                      launchButtons.current.delete(exercise.id);
                    } else {
                      launchButtons.current.set(exercise.id, element);
                    }
                  }}
                  data-exercise-id={exercise.id}
                  onClick={() => {
                    returnFocus.current = exercise.id;
                    setActive(exercise);
                  }}
                >
                  {t("exercises.tryExercise", { title: exercise.title.en })}
                </button>
              </article>
            ))}
          </div>
        </>
      )}
      <ExerciseHistory client={client} offset={historyOffset} onPage={setHistoryOffset} />
    </div>
  );
}
