/**
 * The settings the daily planner reads: preferred exercise types and quiet
 * hours.
 *
 * Preferences are a soft filter, never a wall: the planner prefers exercises
 * matching the chosen tags, and picks an appropriate one anyway when nothing
 * matches. Quiet hours are the one hard rule — while they cover the clock the
 * planner suggests nothing (support options after a positive screening answer
 * remain, because a helpline is not a nudge).
 */

import { useEffect, useState, type ReactElement } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { isApiError, type ApiClient } from "@/lib/api";
import { t } from "@/lib/i18n/i18n";
import { api as defaultClient } from "@/lib/api";

import { fetchPlanSettings, savePlanSettings } from "./endpoints";
import type { PlanSettings } from "./types";

export const planSettingsKeys = {
  current: ["plan", "settings"] as const,
};

/** `HH:MM` from a time input, or a valid default when the field is empty. */
function cleanTime(value: string, fallback: string): string {
  return /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(value) ? value : fallback;
}

export interface PlanSettingsSectionProps {
  client?: ApiClient;
}

export function PlanSettingsSection({
  client = defaultClient,
}: PlanSettingsSectionProps): ReactElement {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [draft, setDraft] = useState<PlanSettings | null>(null);

  const settingsQuery = useQuery<PlanSettings, Error>({
    queryKey: planSettingsKeys.current,
    queryFn: () => fetchPlanSettings(client),
  });

  // The draft mirrors the server until the server has an answer; after that it
  // is the only thing the controls edit, so typing never fights the network.
  useEffect(() => {
    if (settingsQuery.data !== undefined && draft === null) {
      setDraft(settingsQuery.data);
    }
  }, [settingsQuery.data, draft]);

  const save = useMutation<PlanSettings, Error, PlanSettings>({
    mutationFn: (next) =>
      savePlanSettings(
        {
          preferred_exercise_tags: next.preferred_exercise_tags,
          quiet_hours_enabled: next.quiet_hours_enabled,
          quiet_start: cleanTime(next.quiet_start, "22:00"),
          quiet_end: cleanTime(next.quiet_end, "07:00"),
        },
        client,
      ),
    onSuccess: async (saved) => {
      setDraft(saved);
      setMessage(t("plan.settingsSaved"));
      await queryClient.invalidateQueries({ queryKey: planSettingsKeys.current });
    },
    onError: (error) => {
      setMessage(
        isApiError(error)
          ? t("plan.settingsErrorApi")
          : t("plan.settingsErrorGeneric"),
      );
    },
  });

  if (settingsQuery.isLoading) {
    return (
      <section
        aria-labelledby="plan-settings-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
        data-testid="plan-settings"
      >
        <h2 id="plan-settings-heading" className="text-lg">
          {t("plan.settingsHeading")}
        </h2>
        <p className="mt-3 text-sm text-ink-muted" role="status">
          {t("plan.settingsLoading")}
        </p>
      </section>
    );
  }

  if (settingsQuery.isError || draft === null) {
    return (
      <section
        aria-labelledby="plan-settings-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
        data-testid="plan-settings"
      >
        <h2 id="plan-settings-heading" className="text-lg">
          {t("plan.settingsHeading")}
        </h2>
        <p className="mt-3 text-sm text-ink-muted" role="alert">
          {t("plan.settingsLoadError")}
        </p>
      </section>
    );
  }

  const knownTags = draft.known_exercise_tags;

  function toggleTag(tag: string): void {
    setDraft((current) => {
      if (current === null) {
        return current;
      }
      const selected = current.preferred_exercise_tags.includes(tag)
        ? current.preferred_exercise_tags.filter((existing) => existing !== tag)
        : [...current.preferred_exercise_tags, tag];
      return { ...current, preferred_exercise_tags: selected };
    });
  }

  return (
    <section
      aria-labelledby="plan-settings-heading"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      data-testid="plan-settings"
    >
      <h2 id="plan-settings-heading" className="text-lg">
        {t("plan.settingsHeading")}
      </h2>
      <p className="mt-2 text-sm text-ink-muted">
        {t("plan.settingsIntro")}
      </p>

      <fieldset className="mt-4">
        <legend className="font-semibold">{t("plan.settingsTagsLegend")}</legend>
        <p className="mt-1 text-sm text-ink-muted">
          {t("plan.settingsTagsHelp")}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {knownTags.map((tag) => {
            const checked = draft.preferred_exercise_tags.includes(tag);
            return (
              <label
                key={tag}
                className="flex cursor-pointer items-center gap-2 rounded-xl border border-border-strong px-3 py-2"
              >
                <input
                  type="checkbox"
                  name={`plan-tag-${tag}`}
                  data-testid={`plan-tag-${tag}`}
                  checked={checked}
                  onChange={() => toggleTag(tag)}
                  className="h-4 w-4 accent-accent-bg"
                />
                <span className="text-sm font-medium">{tag}</span>
              </label>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="mt-5">
        <legend className="font-semibold">{t("plan.settingsQuietLegend")}</legend>
        <label className="mt-2 inline-flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-4 py-3">
          <input
            type="checkbox"
            role="switch"
            data-testid="quiet-hours-toggle"
            checked={draft.quiet_hours_enabled}
            onChange={(event) => setDraft({ ...draft, quiet_hours_enabled: event.target.checked })}
            className="mt-1 h-5 w-5 accent-accent-bg"
          />
          <span>
            <span className="block font-semibold">{t("plan.settingsQuietToggle")}</span>
            <span className="block text-sm text-ink-muted">
              {t("plan.settingsQuietHelp")}
            </span>
          </span>
        </label>
        <div className="mt-3 flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="text-sm font-semibold">{t("plan.settingsFrom")}</span>
            <input
              type="time"
              value={draft.quiet_start}
              onChange={(event) => setDraft({ ...draft, quiet_start: event.target.value })}
              disabled={!draft.quiet_hours_enabled}
              data-testid="quiet-start"
              className="mt-1 block rounded-xl border border-border-strong bg-surface px-3 py-2 disabled:opacity-50"
            />
          </label>
          <label className="block">
            <span className="text-sm font-semibold">{t("plan.settingsUntil")}</span>
            <input
              type="time"
              value={draft.quiet_end}
              onChange={(event) => setDraft({ ...draft, quiet_end: event.target.value })}
              disabled={!draft.quiet_hours_enabled}
              data-testid="quiet-end"
              className="mt-1 block rounded-xl border border-border-strong bg-surface px-3 py-2 disabled:opacity-50"
            />
          </label>
        </div>
        <p className="mt-2 text-sm text-ink-muted">
          {t("plan.settingsMidnight")}
        </p>
      </fieldset>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => save.mutate(draft)}
          disabled={save.isPending}
          className="rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg disabled:opacity-60"
        >
          {save.isPending ? t("plan.settingsSaving") : t("plan.settingsSave")}
        </button>
        {message !== null && (
          <p role="status" className="text-sm text-ink-muted">
            {message}
          </p>
        )}
      </div>
    </section>
  );
}
