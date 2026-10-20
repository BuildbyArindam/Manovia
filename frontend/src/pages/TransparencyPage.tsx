/**
 * How Manovia's AI works — the transparency page (Day 48).
 *
 * Plain language, localised. Lists every AI component: what it does,
 * what data it uses, what it never does, known limits, and how to turn it off.
 * Each component is a card; the page is reachable from Settings and the footer.
 */

import type { ReactElement } from "react";
import { Link } from "react-router-dom";
import { t } from "@/lib/i18n/i18n";

export const TRANSPARENCY_REVIEWED = "2026-10-20";

type AIComponent = {
  id: string;
  nameRef: string;
  ledeRef: string;
  dataRefs: string[];
  neverRefs: string[];
  limitRefs: string[];
  offRef: string;
};

const COMPONENTS: AIComponent[] = [
  {
    id: "safety-rules",
    nameRef: "transparency.c1Name",
    ledeRef: "transparency.c1Lede",
    dataRefs: ["transparency.c1Data1", "transparency.c1Data2"],
    neverRefs: ["transparency.c1Never1", "transparency.c1Never2"],
    limitRefs: ["transparency.c1Limit1", "transparency.c1Limit2"],
    offRef: "transparency.c1Off",
  },
  {
    id: "safety-ml",
    nameRef: "transparency.c2Name",
    ledeRef: "transparency.c2Lede",
    dataRefs: ["transparency.c2Data1", "transparency.c2Data2"],
    neverRefs: ["transparency.c2Never1", "transparency.c2Never2"],
    limitRefs: ["transparency.c2Limit1", "transparency.c2Limit2"],
    offRef: "transparency.c2Off",
  },
  {
    id: "emotion",
    nameRef: "transparency.c3Name",
    ledeRef: "transparency.c3Lede",
    dataRefs: ["transparency.c3Data1", "transparency.c3Data2"],
    neverRefs: ["transparency.c3Never1", "transparency.c3Never2"],
    limitRefs: ["transparency.c3Limit1", "transparency.c3Limit2"],
    offRef: "transparency.c3Off",
  },
  {
    id: "llm",
    nameRef: "transparency.c4Name",
    ledeRef: "transparency.c4Lede",
    dataRefs: ["transparency.c4Data1", "transparency.c4Data2"],
    neverRefs: ["transparency.c4Never1", "transparency.c4Never2", "transparency.c4Never3"],
    limitRefs: ["transparency.c4Limit1", "transparency.c4Limit2"],
    offRef: "transparency.c4Off",
  },
  {
    id: "output-guard",
    nameRef: "transparency.c5Name",
    ledeRef: "transparency.c5Lede",
    dataRefs: ["transparency.c5Data1"],
    neverRefs: ["transparency.c5Never1"],
    limitRefs: ["transparency.c5Limit1"],
    offRef: "transparency.c5Off",
  },
  {
    id: "retrieval",
    nameRef: "transparency.c6Name",
    ledeRef: "transparency.c6Lede",
    dataRefs: ["transparency.c6Data1", "transparency.c6Data2"],
    neverRefs: ["transparency.c6Never1"],
    limitRefs: ["transparency.c6Limit1", "transparency.c6Limit2"],
    offRef: "transparency.c6Off",
  },
  {
    id: "voice",
    nameRef: "transparency.c7Name",
    ledeRef: "transparency.c7Lede",
    dataRefs: ["transparency.c7Data1", "transparency.c7Data2"],
    neverRefs: ["transparency.c7Never1", "transparency.c7Never2"],
    limitRefs: ["transparency.c7Limit1", "transparency.c7Limit2"],
    offRef: "transparency.c7Off",
  },
  {
    id: "agent",
    nameRef: "transparency.c8Name",
    ledeRef: "transparency.c8Lede",
    dataRefs: ["transparency.c8Data1", "transparency.c8Data2"],
    neverRefs: ["transparency.c8Never1", "transparency.c8Never2"],
    limitRefs: ["transparency.c8Limit1"],
    offRef: "transparency.c8Off",
  },
  {
    id: "forecasting",
    nameRef: "transparency.c9Name",
    ledeRef: "transparency.c9Lede",
    dataRefs: ["transparency.c9Data1"],
    neverRefs: ["transparency.c9Never1", "transparency.c9Never2"],
    limitRefs: ["transparency.c9Limit1", "transparency.c9Limit2"],
    offRef: "transparency.c9Off",
  },
  {
    id: "bandit",
    nameRef: "transparency.c10Name",
    ledeRef: "transparency.c10Lede",
    dataRefs: ["transparency.c10Data1"],
    neverRefs: ["transparency.c10Never1", "transparency.c10Never2"],
    limitRefs: ["transparency.c10Limit1", "transparency.c10Limit2"],
    offRef: "transparency.c10Off",
  },
  {
    id: "private-mode",
    nameRef: "transparency.c11Name",
    ledeRef: "transparency.c11Lede",
    dataRefs: ["transparency.c11Data1", "transparency.c11Data2"],
    neverRefs: ["transparency.c11Never1", "transparency.c11Never2"],
    limitRefs: ["transparency.c11Limit1"],
    offRef: "transparency.c11Off",
  },
  {
    id: "xr",
    nameRef: "transparency.c12Name",
    ledeRef: "transparency.c12Lede",
    dataRefs: ["transparency.c12Data1"],
    neverRefs: ["transparency.c12Never1", "transparency.c12Never2"],
    limitRefs: ["transparency.c12Limit1"],
    offRef: "transparency.c12Off",
  },
  {
    id: "meditation",
    nameRef: "transparency.c13Name",
    ledeRef: "transparency.c13Lede",
    dataRefs: ["transparency.c13Data1"],
    neverRefs: ["transparency.c13Never1"],
    limitRefs: ["transparency.c13Limit1"],
    offRef: "transparency.c13Off",
  },
  {
    id: "biofeedback",
    nameRef: "transparency.c14Name",
    ledeRef: "transparency.c14Lede",
    dataRefs: ["transparency.c14Data1"],
    neverRefs: ["transparency.c14Never1"],
    limitRefs: ["transparency.c14Limit1"],
    offRef: "transparency.c14Off",
  },
];

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

function ComponentCard({ comp }: { comp: AIComponent }): ReactElement {
  return (
    <section
      aria-labelledby={`${comp.id}-heading`}
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      data-testid={`transparency-${comp.id}`}
    >
      <h2 id={`${comp.id}-heading`} className="text-lg">
        {t(comp.nameRef)}
      </h2>
      <p className="mt-2 text-ink-muted">{t(comp.ledeRef)}</p>

      <h3 className="mt-4 font-semibold">{t("transparency.dataHeading")}</h3>
      <ul className="mt-1 list-disc pl-6">
        {comp.dataRefs.map((k) => (
          <li key={k}>{t(k)}</li>
        ))}
      </ul>

      <h3 className="mt-4 font-semibold">{t("transparency.neverHeading")}</h3>
      <ul className="mt-1 list-disc pl-6">
        {comp.neverRefs.map((k) => (
          <li key={k}>{t(k)}</li>
        ))}
      </ul>

      <h3 className="mt-4 font-semibold">{t("transparency.limitsHeading")}</h3>
      <ul className="mt-1 list-disc pl-6">
        {comp.limitRefs.map((k) => (
          <li key={k}>{t(k)}</li>
        ))}
      </ul>

      <p className="mt-4 rounded-xl bg-surface-alt p-3 text-sm">
        <span className="font-semibold">{t("transparency.howToTurnOff")} </span>
        {t(comp.offRef)}
      </p>
    </section>
  );
}

export function TransparencyPage(): ReactElement {
  return (
    <div className="space-y-6" data-testid="transparency-page">
      <div className="rounded-2xl border border-border bg-surface p-6 shadow-soft sm:p-8">
        <h1 className="text-2xl sm:text-3xl">{t("transparency.title")}</h1>
        <p className="mt-3 text-lg text-ink-muted">{t("transparency.lede")}</p>
        <p className="mt-2 text-sm text-ink-muted">
          {t("transparency.reviewed", { date: TRANSPARENCY_REVIEWED })}
        </p>
        <p className="mt-3">
          <Link to="/settings" className="font-semibold text-accent underline underline-offset-4">
            {t("transparency.goToSettings")}
          </Link>
        </p>
      </div>

      <Section id="transparency-principles" heading={t("transparency.principlesHeading")}>
        <ul className="mt-3 list-disc pl-6">
          <li>{t("transparency.principle1")}</li>
          <li>{t("transparency.principle2")}</li>
          <li>{t("transparency.principle3")}</li>
          <li>{t("transparency.principle4")}</li>
        </ul>
      </Section>

      {COMPONENTS.map((c) => (
        <ComponentCard key={c.id} comp={c} />
      ))}

      <Section id="transparency-data" heading={t("transparency.yourDataHeading")}>
        <p className="mt-3">{t("transparency.yourDataBody1")}</p>
        <p className="mt-2">{t("transparency.yourDataBody2")}</p>
        <p className="mt-2">
          <Link to="/privacy" className="font-semibold text-accent underline underline-offset-4">
            {t("transparency.privacyLink")}
          </Link>
        </p>
      </Section>

      <Section id="transparency-questions" heading={t("transparency.questionsHeading")}>
        <p className="mt-3">{t("transparency.questionsBody")}</p>
        <p className="mt-2 text-sm text-ink-muted">{t("transparency.feedbackNote")}</p>
      </Section>
    </div>
  );
}
