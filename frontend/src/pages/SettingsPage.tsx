/**
 * Settings — the one page that is already real on Day 5.
 *
 * It carries the controls the accessibility work needs somewhere to live:
 * palette choice, an honest statement of the reduced-motion setting, the AI
 * disclosure (AGENTS.md rule 3 requires it to be reachable, not just shown once
 * during onboarding), and a way back through onboarding.
 */

import { useState, type FormEvent, type ReactElement } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { deleteChatMemory, fetchChatMemory } from "@/features/chat/endpoints";
import { useMoodHintPreference } from "@/features/chat/useMoodHint";
import { PlanSettingsSection } from "@/features/plan/PlanSettingsSection";
import { PrivacySection } from "@/features/privacy/PrivacySection";
import { signOut } from "@/lib/endpoints";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";
import { useTheme, type ThemePreference } from "@/theme/ThemeProvider";
import { useOnboarding } from "@/onboarding/OnboardingProvider";
import { FALLBACK_AI_DISCLOSURE } from "@/onboarding/copy";
import { t } from "@/lib/i18n/i18n";
import { PageHeader } from "@/components/PageHeader";

function themeOptions(): ReadonlyArray<{ value: ThemePreference; label: string; hint: string }> {
  return [
    { value: "light", label: t("settings.themeLight"), hint: t("settings.themeLightHint") },
    { value: "dark", label: t("settings.themeDark"), hint: t("settings.themeDarkHint") },
    { value: "system", label: t("settings.themeSystem"), hint: t("settings.themeSystemHint") },
  ];
}

export function SettingsPage(): ReactElement {
  const { preference, resolved, setPreference } = useTheme();
  const [showMoodHint, setShowMoodHint] = useMoodHintPreference();
  const { record, reset } = useOnboarding();
  const reduceMotion = usePrefersReducedMotion();
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [confirmMemoryDeletion, setConfirmMemoryDeletion] = useState(false);
  const memoryQuery = useQuery({
    queryKey: ["chat-memory"],
    queryFn: () => fetchChatMemory(api),
    enabled: record !== null,
  });
  const memories = memoryQuery.data?.memories ?? [];
  const deleteMemory = useMutation({
    mutationFn: () => deleteChatMemory(api),
    onSuccess: async () => {
      setConfirmMemoryDeletion(false);
      setMessage(t("settings.memoryDeleted"));
      await queryClient.invalidateQueries({ queryKey: ["chat-memory"] });
    },
    onError: () => {
      setMessage(t("settings.memoryDeleteError"));
    },
  });

  async function handleSignOut(): Promise<void> {
    await signOut(api);
    reset();
    setMessage(t("settings.signedOutMessage"));
  }

  function handleReset(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    reset();
    setMessage(t("settings.onboardingRestarted"));
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("settings.title")}
        lede={t("settings.lede")}
      />

      <section
        aria-labelledby="appearance-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="appearance-heading" className="text-lg">
          {t("settings.appearanceHeading")}
        </h2>
        <fieldset className="mt-4">
          <legend className="text-ink-muted">{t("settings.paletteLegend")}</legend>
          <div className="mt-2 flex flex-wrap gap-3">
            {themeOptions().map((option) => (
              <label
                key={option.value}
                className="flex cursor-pointer items-center gap-2 rounded-xl border border-border-strong px-4 py-2"
              >
                <input
                  type="radio"
                  name="theme"
                  value={option.value}
                  checked={preference === option.value}
                  onChange={() => {
                    setPreference(option.value);
                  }}
                  className="h-4 w-4 accent-accent-bg"
                />
                <span className="font-semibold">{option.label}</span>
                <span className="text-sm text-ink-muted">{option.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <p className="mt-4 text-sm text-ink-muted">
          {t("settings.paletteShowing", { palette: resolved })}
        </p>
        <p className="mt-1 text-sm text-ink-muted">
          {t("settings.motionLine", {
            ask: reduceMotion ? t("settings.motionAsks") : t("settings.motionDoesNotAsk"),
            state: reduceMotion ? t("settings.motionOff") : t("settings.motionOn"),
          })}
        </p>
      </section>

      <PlanSettingsSection client={api} />

      <section
        aria-labelledby="chat-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="chat-heading" className="text-lg">
          {t("settings.chatHeading")}
        </h2>
        <label className="mt-4 inline-flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-4 py-3">
          <input
            type="checkbox"
            role="switch"
            data-testid="mood-hint-toggle"
            checked={showMoodHint}
            onChange={(event) => {
              setShowMoodHint(event.target.checked);
            }}
            className="mt-1 h-5 w-5 accent-accent-bg"
          />
          <span>
            <span className="block font-semibold">{t("settings.moodHintLabel")}</span>
            <span className="block text-sm text-ink-muted">
              {t("settings.moodHintHelp")}
            </span>
          </span>
        </label>
        <p className="mt-3 text-sm text-ink-muted">
          {t("settings.moodHintNote")}
        </p>
      </section>

      <section
        aria-labelledby="memory-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="memory-heading" className="text-lg">
          {t("settings.memoryHeading")}
        </h2>
        <p className="mt-2 text-sm text-ink-muted">
          {t("settings.memoryHelp", { tokens: memoryQuery.data?.max_tokens ?? 300 })}
        </p>
        {record === null ? (
          <p className="mt-4 text-ink-muted">{t("settings.memorySignIn")}</p>
        ) : memoryQuery.isLoading ? (
          <p className="mt-4 text-ink-muted" role="status">
            {t("settings.memoryLoading")}
          </p>
        ) : memoryQuery.isError ? (
          <p className="mt-4 text-ink-muted" role="alert">
            {t("settings.memoryLoadError")}
          </p>
        ) : memories.length === 0 ? (
          <p className="mt-4 text-ink-muted">{t("settings.memoryEmpty")}</p>
        ) : (
          <>
            <ul className="mt-4 space-y-3">
              {memories.map((memory) => (
                <li
                  className="rounded-xl bg-surface-muted p-4 text-ink-muted"
                  key={memory.session_id}
                >
                  {memory.summary}
                </li>
              ))}
            </ul>
            {confirmMemoryDeletion ? (
              <div
                className="mt-4 rounded-xl border border-border-strong p-4"
                role="group"
                aria-labelledby="memory-delete-confirmation"
              >
                <p id="memory-delete-confirmation" className="text-sm text-ink-muted">
                  {t("settings.memoryDeleteConfirm")}
                </p>
                <div className="mt-3 flex flex-wrap gap-3">
                  <button
                    type="button"
                    disabled={deleteMemory.isPending}
                    onClick={() => deleteMemory.mutate()}
                    className="rounded-xl border border-border-strong px-4 py-2 font-semibold disabled:opacity-60"
                  >
                    {deleteMemory.isPending ? t("settings.memoryDeleting") : t("settings.memoryDeleteButton")}
                  </button>
                  <button
                    type="button"
                    disabled={deleteMemory.isPending}
                    onClick={() => setConfirmMemoryDeletion(false)}
                    className="rounded-xl border border-border-strong px-4 py-2 font-semibold disabled:opacity-60"
                  >
                    {t("settings.memoryKeep")}
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmMemoryDeletion(true)}
                className="mt-4 rounded-xl border border-border-strong px-4 py-2 font-semibold"
              >
                {t("settings.memoryDeletePrompt")}
              </button>
            )}
          </>
        )}
      </section>

      <PrivacySection client={api} />

      <section
        aria-labelledby="ai-disclosure-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="ai-disclosure-heading" className="text-lg">
          {t("settings.aiHeading")}
        </h2>
        <p className="mt-3 rounded-xl bg-surface-muted p-4 text-ink-muted">
          {FALLBACK_AI_DISCLOSURE}
        </p>
      </section>

      <section
        aria-labelledby="account-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="account-heading" className="text-lg">
          {t("settings.accountHeading")}
        </h2>
        <dl className="mt-3 space-y-2 text-ink-muted">
          <div>
            <dt className="inline font-semibold text-ink">{t("settings.accountKindLabel")} </dt>
            <dd className="inline">
              {record?.authMode === "account" ? t("settings.accountKindEmail") : t("settings.accountKindGuest")}
            </dd>
          </div>
          <div>
            <dt className="inline font-semibold text-ink">{t("settings.accountEmailLabel")} </dt>
            <dd className="inline">{record?.user.email ?? t("settings.accountEmailNone")}</dd>
          </div>
          <div>
            <dt className="inline font-semibold text-ink">{t("settings.accountAgreementsLabel")} </dt>
            <dd className="inline">
              {Object.entries(record?.consents ?? {})
                .filter(([, granted]) => granted)
                .map(([kind]) => kind)
                .join(", ") || t("settings.accountAgreementsNone")}
            </dd>
          </div>
        </dl>
        <form className="mt-5 flex flex-wrap gap-3" onSubmit={handleReset}>
          <button
            type="submit"
            className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
          >
            {t("settings.restartOnboarding")}
          </button>
          <button
            type="button"
            className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
            onClick={() => {
              void handleSignOut();
            }}
          >
            {t("settings.signOut")}
          </button>
        </form>
        {message !== null ? (
          <p role="status" className="mt-4 rounded-xl bg-accent-soft p-4">
            {message}
          </p>
        ) : null}
      </section>
    </div>
  );
}
