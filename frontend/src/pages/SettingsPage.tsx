/**
 * Settings — the one page that is already real on Day 5.
 *
 * It carries the controls the accessibility work needs somewhere to live:
 * palette choice, an honest statement of the reduced-motion setting, the AI
 * disclosure (AGENTS.md rule 3 requires it to be reachable, not just shown once
 * during onboarding), and a way back through onboarding.
 */

import { useState, type FormEvent, type ReactElement } from "react";

import { api } from "@/lib/api";
import { signOut } from "@/lib/endpoints";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";
import { useTheme, type ThemePreference } from "@/theme/ThemeProvider";
import { useOnboarding } from "@/onboarding/OnboardingProvider";
import { FALLBACK_AI_DISCLOSURE } from "@/onboarding/copy";
import { PageHeader } from "@/components/PageHeader";

const THEME_OPTIONS: ReadonlyArray<{ value: ThemePreference; label: string; hint: string }> = [
  { value: "light", label: "Light", hint: "Warm paper" },
  { value: "dark", label: "Dark", hint: "Warm charcoal" },
  { value: "system", label: "Match my device", hint: "Follow the system setting" },
];

export function SettingsPage(): ReactElement {
  const { preference, resolved, setPreference } = useTheme();
  const { record, reset } = useOnboarding();
  const reduceMotion = usePrefersReducedMotion();
  const [message, setMessage] = useState<string | null>(null);

  async function handleSignOut(): Promise<void> {
    await signOut(api);
    reset();
    setMessage("You are signed out. Starting again gives you a fresh guest account.");
  }

  function handleReset(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    reset();
    setMessage("Onboarding restarted. You will see the welcome screen again.");
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        lede="Small choices, all reversible. Nothing here changes what Manovia is."
      />

      <section
        aria-labelledby="appearance-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="appearance-heading" className="text-lg">
          Appearance
        </h2>
        <fieldset className="mt-4">
          <legend className="text-ink-muted">Palette</legend>
          <div className="mt-2 flex flex-wrap gap-3">
            {THEME_OPTIONS.map((option) => (
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
          Showing the {resolved} palette. Both palettes are checked for 4.5:1 text contrast.
        </p>
        <p className="mt-1 text-sm text-ink-muted">
          Your device {reduceMotion ? "asks for" : "does not ask for"} reduced motion, so animations
          are {reduceMotion ? "off" : "on"}.
        </p>
      </section>

      <section
        aria-labelledby="ai-disclosure-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="ai-disclosure-heading" className="text-lg">
          About the AI
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
          Your account
        </h2>
        <dl className="mt-3 space-y-2 text-ink-muted">
          <div>
            <dt className="inline font-semibold text-ink">Kind: </dt>
            <dd className="inline">
              {record?.authMode === "account" ? "Email account" : "Guest (anonymous)"}
            </dd>
          </div>
          <div>
            <dt className="inline font-semibold text-ink">Email: </dt>
            <dd className="inline">{record?.user.email ?? "none — you are anonymous"}</dd>
          </div>
          <div>
            <dt className="inline font-semibold text-ink">Agreements: </dt>
            <dd className="inline">
              {Object.entries(record?.consents ?? {})
                .filter(([, granted]) => granted)
                .map(([kind]) => kind)
                .join(", ") || "none recorded"}
            </dd>
          </div>
        </dl>
        <form className="mt-5 flex flex-wrap gap-3" onSubmit={handleReset}>
          <button
            type="submit"
            className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
          >
            Start onboarding again
          </button>
          <button
            type="button"
            className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
            onClick={() => {
              void handleSignOut();
            }}
          >
            Sign out
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
