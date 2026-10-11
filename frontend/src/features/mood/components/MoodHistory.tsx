/**
 * Recent check-ins — and the ability to take one back.
 *
 * The list is for *the person's own* record: what they chose, in their words,
 * with their note. No scores are computed from it, nothing is compared to an
 * average, and there is no "streak at risk" nudge attached to it.
 *
 * Deletion is a two-step confirmation with the honest sentence about what it
 * does. It is not hidden behind a settings page, because a check-in somebody
 * regrets writing should be one obvious action away from gone.
 */

import { useState, type ReactElement } from "react";

import { emotionLabel, energyPoint, factorLabel, valencePoint } from "../taxonomy";
import { MOOD_COPY } from "../copy";
import type { MoodEntry } from "../types";
import { useDeleteMoodEntry, useMoodHistory } from "../useMood";
import type { ApiClient } from "@/lib/api";
import { MoodFace } from "./MoodFace";

export interface MoodHistoryProps {
  client?: ApiClient;
}

/** `2026-03-15` → `15 Mar 2026`, in the reader's own locale. */
export function formatLocalDay(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  if (!year || !month || !day) {
    return isoDate;
  }
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function MoodHistory({ client }: MoodHistoryProps): ReactElement {
  const history = useMoodHistory(client);
  const deletion = useDeleteMoodEntry(client);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const entries = history.data?.entries ?? [];

  return (
    <section
      aria-labelledby="mood-history-heading"
      data-testid="mood-history"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id="mood-history-heading" className="text-lg">
        {MOOD_COPY.historyHeading}
      </h2>

      {history.isPending ? <p className="mt-2 text-ink-muted">{MOOD_COPY.historyLoading}</p> : null}
      {history.isError ? (
        <p className="mt-2 text-danger" role="alert">
          {MOOD_COPY.historyError}
        </p>
      ) : null}
      {!history.isPending && !history.isError && entries.length === 0 ? (
        <p className="mt-2 text-ink-muted">{MOOD_COPY.historyEmpty}</p>
      ) : null}

      {status !== null ? (
        <p className="mt-2 text-sm text-ink-muted" role="status">
          {status}
        </p>
      ) : null}

      <ul className="mt-4 space-y-4">
        {entries.map((entry: MoodEntry) => {
          const valence = valencePoint(entry.valence);
          const energy = energyPoint(entry.energy);
          const day = formatLocalDay(entry.local_date);
          return (
            <li
              key={entry.id}
              data-testid="mood-history-entry"
              className="rounded-xl border border-border p-4"
            >
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-ink-muted" aria-hidden="true">
                  <MoodFace shape={valence.face} />
                </span>
                <p className="text-base">
                  <time dateTime={entry.local_date}>{day}</time> · {valence.label} · energy {energy.label}
                </p>
              </div>

              {entry.emotions.length > 0 || entry.factors.length > 0 ? (
                <ul className="mt-2 flex flex-wrap gap-2 text-sm text-ink-muted">
                  {entry.emotions.map((id) => (
                    <li key={`emotion-${id}`} className="rounded-full bg-accent-soft px-3 py-1">
                      {emotionLabel(id)}
                    </li>
                  ))}
                  {entry.factors.map((id) => (
                    <li key={`factor-${id}`} className="rounded-full bg-surface-muted px-3 py-1">
                      {factorLabel(id)}
                    </li>
                  ))}
                </ul>
              ) : null}

              {entry.note !== null ? (
                <p className="mt-2 whitespace-pre-wrap text-ink-muted">{entry.note}</p>
              ) : null}

              {confirming === entry.id ? (
                <div className="mt-3 rounded-xl bg-surface-muted p-3">
                  <p className="font-semibold">{MOOD_COPY.historyDeleteConfirm}</p>
                  <p className="mt-1 text-sm text-ink-muted">{MOOD_COPY.historyDeleteConfirmBody}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="min-h-12 rounded-xl bg-danger-bg px-4 text-sm font-semibold text-danger-fg"
                      onClick={() => {
                        deletion.mutate(entry.id, {
                          onSuccess: () => {
                            setConfirming(null);
                            setStatus(MOOD_COPY.historyDeleted);
                          },
                        });
                      }}
                    >
                      {MOOD_COPY.historyDeleteYes}
                    </button>
                    <button
                      type="button"
                      className="min-h-12 rounded-xl border border-border px-4 text-sm font-semibold"
                      onClick={() => {
                        setConfirming(null);
                      }}
                    >
                      {MOOD_COPY.historyDeleteNo}
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  className="mt-3 min-h-12 rounded-xl border border-border px-4 text-sm"
                  onClick={() => {
                    setConfirming(entry.id);
                    setStatus(null);
                  }}
                >
                  {MOOD_COPY.historyDelete(day)}
                </button>
              )}
            </li>
          );
        })}
      </ul>

    </section>
  );
}
