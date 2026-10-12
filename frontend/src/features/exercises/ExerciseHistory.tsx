import { useQuery } from "@tanstack/react-query";
import { t } from "@/lib/i18n/i18n";
import { formatDateTime } from "@/lib/dates";
import type { ReactElement } from "react";

import type { ApiClient } from "@/lib/api";

import { EXERCISE_COPY as COPY, formatDuration } from "./copy";
import { EXERCISE_HISTORY_KEY, fetchExerciseHistory } from "./endpoints";

interface ExerciseHistoryProps {
  client?: ApiClient;
  offset: number;
  onPage: (offset: number) => void;
}

export function ExerciseHistory({ client, offset, onPage }: ExerciseHistoryProps): ReactElement {
  const history = useQuery({
    queryKey: [...EXERCISE_HISTORY_KEY, offset],
    queryFn: () => fetchExerciseHistory(offset, client),
    retry: false,
  });
  return (
    <section
      className="space-y-4 rounded-2xl border border-border bg-surface p-5 shadow-soft sm:p-8"
      aria-labelledby="exercise-history-heading"
      data-testid="exercise-history"
    >
      <h2 id="exercise-history-heading" className="text-xl">
        {COPY.historyHeading}
      </h2>
      <p className="text-sm text-ink-muted">{COPY.historyNote}</p>
      {history.isPending ? <p>{t("exercises.historyLoading")}</p> : null}
      {history.isError ? (
        <div>
          <p role="alert">{COPY.historyError}</p>
          <button
            type="button"
            className="btn-secondary mt-2"
            onClick={() => {
              void history.refetch();
            }}
          >
            Retry history
          </button>
        </div>
      ) : null}
      {history.data?.items.length === 0 ? <p>{COPY.historyEmpty}</p> : null}
      <ol className="space-y-3">
        {history.data?.items.map((entry) => (
          <li
            key={entry.id}
            className="space-y-2 rounded-xl border border-border p-4"
            data-testid="exercise-history-entry"
          >
            <h3 className="font-semibold">{entry.title}</h3>
            <p className="text-sm text-ink-muted">
              <time dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time>{" "}
              · {formatDuration(entry.duration_seconds)}
            </p>
            <p className="text-sm">
              Before: {entry.pre_rating === null ? "not rated" : `${entry.pre_rating} of 5`} ·
              After: {entry.post_rating === null ? "not rated" : `${entry.post_rating} of 5`}
            </p>
            <p>{entry.summary}</p>
          </li>
        ))}
      </ol>
      {offset > 0 || history.data?.has_more ? (
        <div className="flex flex-wrap gap-3" aria-label={t("exercises.historyPagesLabel")}>
          <button
            type="button"
            className="btn-secondary"
            disabled={offset === 0}
            onClick={() => onPage(Math.max(0, offset - 20))}
          >
            Newer completions
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={!history.data?.has_more}
            onClick={() => onPage(offset + 20)}
          >
            Older completions
          </button>
        </div>
      ) : null}
    </section>
  );
}
