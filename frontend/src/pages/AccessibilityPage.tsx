/**
 * The accessibility statement (Day 25).
 *
 * What Manovia aims for (WCAG 2.2 AA), what it does to get there, how that is
 * checked, what is known not to work yet, and how to say so. Reachable from the
 * footer, from Settings, and — because whether an app will work for you is
 * something to know before agreeing to anything — before onboarding, at
 * `/accessibility`.
 *
 * The statement is honest by construction: every "how we check" line maps to a
 * job that actually runs (`.github/workflows/a11y-lighthouse.yml`), and the
 * limitations list is the one in PROGRESS.md, not a marketing summary.
 */

import type { ReactElement } from "react";
import { Link } from "react-router-dom";

import { formatLocalDay } from "@/lib/dates";
import { t } from "@/lib/i18n/i18n";
import { useOnboarding } from "@/onboarding/OnboardingProvider";

/** The date this statement was last reviewed against the running app. */
export const STATEMENT_REVIEWED = "2026-10-12";

/** Where accessibility problems can be reported, in public. */
export const FEEDBACK_URL = "https://github.com/BuildbyArindam/Manovia/issues";

const MEASURES = [
  "a11y.do1",
  "a11y.do2",
  "a11y.do3",
  "a11y.do4",
  "a11y.do5",
  "a11y.do6",
  "a11y.do7",
  "a11y.do8",
  "a11y.do9",
  "a11y.do10",
] as const;

const CHECKS = ["a11y.check1", "a11y.check2", "a11y.check3", "a11y.check4"] as const;

const LIMITS = ["a11y.limit1", "a11y.limit2", "a11y.limit3", "a11y.limit4", "a11y.limit5"] as const;

function Section({
  id,
  heading,
  children,
}: {
  id: string;
  heading: string;
  children: ReactElement | ReactElement[];
}): ReactElement {
  return (
    <section
      aria-labelledby={id}
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id={id} className="text-xl">
        {heading}
      </h2>
      {children}
    </section>
  );
}

export function AccessibilityPage(): ReactElement {
  const { isComplete } = useOnboarding();

  return (
    <div className="space-y-6" data-testid="accessibility-page">
      <div className="rounded-2xl border border-border bg-surface p-6 shadow-soft sm:p-8">
        <h1 className="text-2xl sm:text-3xl">{t("a11y.title")}</h1>
        <p className="mt-3 text-lg text-ink-muted">{t("a11y.lede")}</p>
      </div>

      <Section id="a11y-aim" heading={t("a11y.aimHeading")}>
        <p className="mt-3">{t("a11y.aimBody")}</p>
      </Section>

      <Section id="a11y-measures" heading={t("a11y.doHeading")}>
        <ul className="mt-3 list-disc space-y-2 pl-6">
          {MEASURES.map((key) => (
            <li key={key}>{t(key)}</li>
          ))}
        </ul>
      </Section>

      <Section id="a11y-checks" heading={t("a11y.checkHeading")}>
        <ul className="mt-3 list-disc space-y-2 pl-6">
          {CHECKS.map((key) => (
            <li key={key}>{t(key)}</li>
          ))}
        </ul>
      </Section>

      <Section id="a11y-limits" heading={t("a11y.limitsHeading")}>
        <ul className="mt-3 list-disc space-y-2 pl-6">
          {LIMITS.map((key) => (
            <li key={key}>{t(key)}</li>
          ))}
        </ul>
      </Section>

      <Section id="a11y-feedback" heading={t("a11y.feedbackHeading")}>
        <p className="mt-3">{t("a11y.feedbackBody")}</p>
        <p className="mt-3">
          <a
            href={FEEDBACK_URL}
            rel="noopener noreferrer"
            target="_blank"
            className="inline-flex min-h-11 items-center font-semibold text-accent underline underline-offset-4"
          >
            {t("a11y.feedbackLink")}
            <span className="sr-only"> {t("a11y.opensInNewTab")}</span>
          </a>
        </p>
        <p className="mt-2 text-sm text-ink-muted">{t("a11y.feedbackPrivacy")}</p>
      </Section>

      <p className="text-sm text-ink-muted" data-testid="a11y-reviewed">
        {t("a11y.reviewed", { date: formatLocalDay(STATEMENT_REVIEWED) })}
      </p>

      {isComplete ? null : (
        <p>
          <Link
            to="/"
            className="inline-flex min-h-11 items-center font-semibold text-accent underline underline-offset-4"
          >
            {t("a11y.backToStart")}
          </Link>
        </p>
      )}
    </div>
  );
}
