/**
 * The onboarding flow: welcome → what Manovia is and is not → AI disclosure →
 * agreements → choose guest or create an account.
 *
 * Design notes:
 * - Every agreement is a separate checkbox and every one starts unticked. The
 *   "continue" button on the agreements step is disabled until the three
 *   required ones are ticked, and `OnboardingProvider.complete()` re-checks the
 *   same rule, so the gate cannot be lost in a refactor.
 * - Steps move focus to their own heading, so a keyboard user is never left at
 *   the bottom of a page that just changed, and the change is announced in a
 *   polite live region.
 * - Motion is optional: the card animates in only when the user has not asked
 *   for reduced motion (`usePrefersReducedMotion`).
 */

import { useEffect, useRef, useState, type FormEvent, type ReactElement } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import {
  ALL_CONSENTS,
  REQUIRED_CONSENTS,
  fetchConsentRequirements,
  type ConsentKind,
  type ConsentRequirement,
  type ConsentRequirementsResponse,
} from "@/lib/endpoints";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";
import { t } from "@/lib/i18n/i18n";
import {
  FALLBACK_AI_DISCLOSURE,
  FALLBACK_CONSENT_VERSION,
  aiDisclosureHeadingCopy,
  consentCopy,
  consentCopyNote,
  scopeCopy,
  welcomeCopy,
} from "./copy";
import { useOnboarding } from "./OnboardingProvider";
import { NO_CONSENTS, type AuthMode, type ConsentState } from "./storage";

type StepId = "welcome" | "scope" | "ai" | "consent" | "account";

interface Step {
  id: StepId;
  label: string;
  heading: string;
}

/** Re-evaluated per render so the labels follow the chosen language. */
function steps(): readonly Step[] {
  return [
    { id: "welcome", label: t("onboarding.steps.welcomeLabel"), heading: t("onboarding.welcomeHeading") },
    { id: "scope", label: t("onboarding.steps.scopeLabel"), heading: t("onboarding.scopeHeading") },
    { id: "ai", label: t("onboarding.steps.aiLabel"), heading: t("onboarding.aiHeading") },
    { id: "consent", label: t("onboarding.steps.consentLabel"), heading: t("onboarding.steps.consentHeading") },
    { id: "account", label: t("onboarding.steps.accountLabel"), heading: t("onboarding.steps.accountHeading") },
  ];
}

const HEADING_ID = "onboarding-heading";

export function OnboardingFlow(): ReactElement {
  const { complete } = useOnboarding();
  const reduceMotion = usePrefersReducedMotion();

  const [stepIndex, setStepIndex] = useState(0);
  const [consents, setConsents] = useState<ConsentState>(NO_CONSENTS);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showAccountForm, setShowAccountForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  const headingRef = useRef<HTMLHeadingElement>(null);

  const requirements = useQuery<ConsentRequirementsResponse>({
    queryKey: ["consent-requirements"],
    queryFn: () => fetchConsentRequirements(api),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });

  const allRequiredGranted = REQUIRED_CONSENTS.every((kind) => consents[kind] === true);

  // Focus follows the step, so keyboard and screen-reader users are moved with
  // the content instead of being stranded where the old step ended.
  useEffect(() => {
    headingRef.current?.focus();
    const allSteps = steps();
    const current = allSteps[stepIndex];
    if (current !== undefined) {
      setAnnouncement(
        t("onboarding.stepAnnouncement", {
          current: stepIndex + 1,
          total: allSteps.length,
          heading: current.heading,
        }),
      );
    }
  }, [stepIndex]);

  const allSteps = steps();
  const step = allSteps[stepIndex];
  if (step === undefined) {
    // Unreachable (stepIndex is clamped), but noUncheckedIndexedAccess makes the
    // narrowing necessary. Every hook has already run, so the order holds.
    return <div className="min-h-screen bg-bg" />;
  }

  function toggleConsent(kind: ConsentKind): void {
    setConsents((previous) => ({ ...previous, [kind]: previous[kind] !== true }));
    setError(null);
  }

  function goTo(index: number): void {
    setError(null);
    setStepIndex(Math.min(Math.max(index, 0), steps().length - 1));
  }

  function handleConsentContinue(): void {
    if (!allRequiredGranted) {
      setError(t("onboarding.gateError"));
      return;
    }
    goTo(stepIndex + 1);
  }

  async function handleFinish(mode: AuthMode): Promise<void> {
    setSubmitting(true);
    setError(null);
    const result = await complete({
      consents,
      authMode: mode,
      credentials: mode === "account" ? { email, password } : undefined,
    });
    setSubmitting(false);
    if (!result.ok) {
      setError(result.error);
    }
  }

  function handleAccountSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void handleFinish("account");
  }

  return (
    <div className="min-h-screen bg-bg text-ink">
      <a className="skip-link" href="#onboarding-main">
        {t("onboarding.skipLink")}
      </a>
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <p className="font-serif text-lg font-semibold">Manovia</p>
          <p className="text-sm text-ink-muted">
            {t("onboarding.stepCounter", { current: stepIndex + 1, total: allSteps.length })}
          </p>
        </div>
      </header>

      <main id="onboarding-main" tabIndex={-1} className="mx-auto max-w-3xl px-4 py-8 sm:py-12">
        <nav aria-label={t("onboarding.navLabel")}>
          <ol className="flex flex-wrap gap-2 text-sm">
            {allSteps.map((entry, index) => {
              const state =
                index === stepIndex ? "current" : index < stepIndex ? "done" : "upcoming";
              return (
                <li
                  key={entry.id}
                  aria-current={state === "current" ? "step" : undefined}
                  className={
                    state === "current"
                      ? "rounded-full border border-border-strong bg-accent-soft px-3 py-1 font-semibold text-ink"
                      : "rounded-full border border-border px-3 py-1 text-ink-muted"
                  }
                >
                  <span className="sr-only">{state === "done" ? t("onboarding.completedPrefix") : ""}</span>
                  {entry.label}
                </li>
              );
            })}
          </ol>
        </nav>

        <section
          aria-labelledby={HEADING_ID}
          className={
            reduceMotion
              ? "mt-8 rounded-2xl border border-border bg-surface p-6 shadow-soft sm:p-8"
              : "mt-8 animate-rise-in rounded-2xl border border-border bg-surface p-6 shadow-soft sm:p-8"
          }
        >
          <h1 id={HEADING_ID} ref={headingRef} tabIndex={-1} className="text-2xl sm:text-3xl">
            {step.heading}
          </h1>

          {step.id === "welcome" ? (
            <div className="mt-4 space-y-4">
              <p className="text-lg">{welcomeCopy().intro}</p>
              <ul className="list-disc space-y-2 pl-6 text-ink-muted">
                {welcomeCopy().points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
              <p className="rounded-xl bg-accent-soft p-4 text-ink">{welcomeCopy().reassurance}</p>
            </div>
          ) : null}

          {step.id === "scope" ? (
            <div className="mt-4 space-y-6">
              <p className="text-ink-muted">{scopeCopy().intro}</p>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="rounded-xl border border-border bg-accent-soft p-4">
                  <h2 className="text-lg">{t("onboarding.scopeIsHeading")}</h2>
                  <ul className="mt-2 list-disc space-y-2 pl-5 text-ink-muted">
                    {scopeCopy().is.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
                <div className="rounded-xl border border-border bg-danger-soft p-4">
                  <h2 className="text-lg">{t("onboarding.scopeIsNotHeading")}</h2>
                  <ul className="mt-2 list-disc space-y-2 pl-5 text-ink-muted">
                    {scopeCopy().isNot.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          ) : null}

          {step.id === "ai" ? (
            <div className="mt-4 space-y-4">
              <p className="text-ink-muted">{aiDisclosureHeadingCopy().intro}</p>
              <blockquote
                data-testid="ai-disclosure"
                className="rounded-xl border border-border bg-surface-muted p-4 text-ink"
              >
                {aiDisclosureText(requirements.data)}
              </blockquote>
              {requirements.isError ? (
                <p className="text-sm text-ink-muted">
                  {t("onboarding.aiFallbackNote")}
                </p>
              ) : null}
            </div>
          ) : null}

          {step.id === "consent" ? (
            <fieldset className="mt-6">
              <legend className="sr-only">{t("onboarding.consentLegend")}</legend>
              <p className="text-ink-muted">{consentCopyNote()}</p>
              <ul className="mt-4 space-y-4">
                {ALL_CONSENTS.map((kind) => (
                  <li key={kind} className="rounded-xl border border-border bg-surface-muted p-4">
                    <label className="flex cursor-pointer gap-3" htmlFor={`consent-${kind}`}>
                      <input
                        id={`consent-${kind}`}
                        type="checkbox"
                        className="mt-1 h-5 w-5 shrink-0 accent-accent-bg"
                        checked={consents[kind]}
                        aria-required={consentCopy()[kind].required}
                        aria-describedby={`consent-${kind}-summary`}
                        onChange={() => toggleConsent(kind)}
                      />
                      <span className="block">
                        <span className="block font-semibold">
                          {requirementFor(requirements.data, kind)?.title ??
                            consentCopy()[kind].title}
                          <span className="ml-2 text-sm font-normal text-ink-muted">
                            {t("onboarding.versionNote", {
                              version:
                                requirementFor(requirements.data, kind)?.version ??
                                FALLBACK_CONSENT_VERSION,
                            })}
                          </span>
                        </span>
                        <span id={`consent-${kind}-summary`} className="mt-1 block text-ink-muted">
                          {requirementFor(requirements.data, kind)?.summary ??
                            consentCopy()[kind].summary}
                        </span>
                        <span className="mt-2 inline-block rounded-full border border-border-strong px-2 py-0.5 text-xs font-semibold uppercase tracking-wide">
                          {consentCopy()[kind].required ? t("onboarding.requiredBadge") : t("onboarding.optionalBadge")}
                        </span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </fieldset>
          ) : null}

          {step.id === "account" ? (
            <div className="mt-6 space-y-5">
              <p className="text-ink-muted">
                {t("onboarding.accountIntro")}
              </p>
              <button
                type="button"
                className="w-full rounded-xl bg-accent-bg px-5 py-3 text-lg font-semibold text-accent-fg disabled:opacity-60 sm:w-auto"
                disabled={submitting}
                onClick={() => {
                  void handleFinish("guest");
                }}
              >
                {submitting ? t("onboarding.guestStarting") : t("onboarding.guestButton")}
              </button>
              <div className="border-t border-border pt-5">
                <button
                  type="button"
                  aria-expanded={showAccountForm}
                  aria-controls="account-form"
                  className="rounded-xl border border-border-strong px-5 py-3 font-semibold"
                  onClick={() => {
                    setShowAccountForm((value) => !value);
                  }}
                >
                  {t("onboarding.createToggle")}
                </button>
                {showAccountForm ? (
                  <form
                    id="account-form"
                    className="mt-4 max-w-md space-y-4"
                    onSubmit={handleAccountSubmit}
                  >
                    <div>
                      <label htmlFor="onboarding-email" className="block font-semibold">
                        {t("onboarding.emailLabel")}
                      </label>
                      <input
                        id="onboarding-email"
                        type="email"
                        autoComplete="email"
                        required
                        value={email}
                        onChange={(event) => {
                          setEmail(event.target.value);
                        }}
                        className="mt-1 w-full rounded-xl border border-border-strong bg-surface px-4 py-2"
                      />
                    </div>
                    <div>
                      <label htmlFor="onboarding-password" className="block font-semibold">
                        {t("onboarding.passwordLabel")}
                      </label>
                      <input
                        id="onboarding-password"
                        type="password"
                        autoComplete="new-password"
                        required
                        minLength={10}
                        value={password}
                        onChange={(event) => {
                          setPassword(event.target.value);
                        }}
                        className="mt-1 w-full rounded-xl border border-border-strong bg-surface px-4 py-2"
                      />
                      <p className="mt-1 text-sm text-ink-muted">
                        {t("onboarding.passwordHelp")}
                      </p>
                    </div>
                    <button
                      type="submit"
                      className="rounded-xl bg-accent-bg px-5 py-3 font-semibold text-accent-fg disabled:opacity-60"
                      disabled={submitting}
                    >
                      {submitting ? t("onboarding.accountCreating") : t("onboarding.createSubmit")}
                    </button>
                  </form>
                ) : null}
              </div>
            </div>
          ) : null}

          {error !== null ? (
            <p
              role="alert"
              className="mt-6 rounded-xl bg-danger-soft p-4 font-semibold text-danger"
            >
              {error}
            </p>
          ) : null}

          <div className="mt-8 flex items-center justify-between gap-4">
            <button
              type="button"
              className="rounded-xl border border-border-strong px-5 py-2 font-semibold disabled:invisible"
              disabled={stepIndex === 0}
              onClick={() => {
                goTo(stepIndex - 1);
              }}
            >
              {t("onboarding.backButton")}
            </button>
            {step.id === "consent" ? (
              <button
                type="button"
                className="rounded-xl bg-accent-bg px-5 py-3 font-semibold text-accent-fg disabled:opacity-60"
                disabled={!allRequiredGranted}
                aria-describedby="consent-hint"
                onClick={handleConsentContinue}
              >
                {t("onboarding.continueButton")}
              </button>
            ) : null}
            {step.id !== "consent" && step.id !== "account" ? (
              <button
                type="button"
                className="rounded-xl bg-accent-bg px-5 py-3 font-semibold text-accent-fg"
                onClick={() => {
                  goTo(stepIndex + 1);
                }}
              >
                {t("onboarding.continueButton")}
              </button>
            ) : null}
          </div>
          {step.id === "consent" ? (
            <p id="consent-hint" className="mt-3 text-sm text-ink-muted">
              {allRequiredGranted
                ? t("onboarding.consentHintGranted")
                : t("onboarding.consentHintPending")}
            </p>
          ) : null}
        </section>

        <p className="mt-8 text-sm text-ink-muted">
          {t("onboarding.footer")}
        </p>
      </main>

      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>
    </div>
  );
}

function requirementFor(
  data: ConsentRequirementsResponse | undefined,
  kind: ConsentKind,
): ConsentRequirement | undefined {
  return data?.documents.find((entry) => entry.kind === kind);
}

function aiDisclosureText(data: ConsentRequirementsResponse | undefined): string {
  return requirementFor(data, "ai_disclosure")?.text ?? FALLBACK_AI_DISCLOSURE;
}
