/**
 * Accessibility statement (Day 25, WCAG 2.2 Level AA).
 *
 * Reachable from the footer on every page and from Settings. States what
 * Manovia conforms to, how keyboard/screen-reader/zoom/reduced-motion/offline
 * features work, what known limitations remain, and offers direct high-contrast
 * and palette controls right on the page so a reader does not have to go
 * hunting for them.
 */

import type { ReactElement } from "react";
import { Link } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { t } from "@/lib/i18n/i18n";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";
import {
  useTheme,
  type ContrastPreference,
  type ThemePreference,
} from "@/theme/ThemeProvider";

function contrastOptions(): ReadonlyArray<{
  value: ContrastPreference;
  label: string;
  hint: string;
}> {
  return [
    {
      value: "normal",
      label: t("settings.contrastNormal"),
      hint: t("settings.contrastNormalHint"),
    },
    {
      value: "high",
      label: t("settings.contrastHigh"),
      hint: t("settings.contrastHighHint"),
    },
    {
      value: "system",
      label: t("settings.contrastSystem"),
      hint: t("settings.contrastSystemHint"),
    },
  ];
}

function paletteOptions(): ReadonlyArray<{
  value: ThemePreference;
  label: string;
}> {
  return [
    { value: "light", label: t("settings.themeLight") },
    { value: "dark", label: t("settings.themeDark") },
    { value: "system", label: t("settings.themeSystem") },
  ];
}

export function AccessibilityPage(): ReactElement {
  const {
    preference,
    resolved,
    setPreference,
    contrastPreference,
    highContrast,
    setContrastPreference,
  } = useTheme();
  const reduceMotion = usePrefersReducedMotion();

  return (
    <div className="space-y-6" data-testid="accessibility-page">
      <PageHeader
        title={t("accessibility.title")}
        lede={t("accessibility.lede")}
      />

      <section
        aria-labelledby="a11y-conformance-heading"
        className="space-y-3 rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="a11y-conformance-heading" className="text-lg">
          {t("accessibility.conformanceHeading")}
        </h2>
        <p className="text-ink-muted">{t("accessibility.conformanceBody")}</p>
        <ul className="list-disc space-y-2 pl-5 text-ink-muted">
          <li>{t("accessibility.measureContrast")}</li>
          <li>{t("accessibility.measureKeyboard")}</li>
          <li>{t("accessibility.measureTargetSize")}</li>
          <li>{t("accessibility.measureZoom")}</li>
          <li>{t("accessibility.measureMotion")}</li>
          <li>{t("accessibility.measureNoTimeouts")}</li>
        </ul>
      </section>

      <section
        aria-labelledby="a11y-controls-heading"
        className="space-y-4 rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="a11y-controls-heading" className="text-lg">
          {t("accessibility.controlsHeading")}
        </h2>
        <p className="text-sm text-ink-muted">
          {t("accessibility.controlsIntro")}
        </p>

        <fieldset>
          <legend className="font-semibold text-ink">
            {t("settings.contrastLegend")}
          </legend>
          <div className="mt-2 flex flex-wrap gap-3">
            {contrastOptions().map((option) => (
              <label
                key={option.value}
                className="flex min-h-11 cursor-pointer items-center gap-2 rounded-xl border border-border-strong px-4 py-2"
              >
                <input
                  type="radio"
                  name="a11y-contrast"
                  value={option.value}
                  checked={contrastPreference === option.value}
                  onChange={() => {
                    setContrastPreference(option.value);
                  }}
                  className="h-4 w-4 accent-accent-bg"
                />
                <span className="font-semibold">{option.label}</span>
                <span className="text-sm text-ink-muted">{option.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend className="font-semibold text-ink">
            {t("settings.paletteLegend")}
          </legend>
          <div className="mt-2 flex flex-wrap gap-3">
            {paletteOptions().map((option) => (
              <label
                key={option.value}
                className="flex min-h-11 cursor-pointer items-center gap-2 rounded-xl border border-border-strong px-4 py-2"
              >
                <input
                  type="radio"
                  name="a11y-palette"
                  value={option.value}
                  checked={preference === option.value}
                  onChange={() => {
                    setPreference(option.value);
                  }}
                  className="h-4 w-4 accent-accent-bg"
                />
                <span className="font-semibold">{option.label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <p className="text-sm text-ink-muted" data-testid="a11y-active-state">
          {t("accessibility.activeSummary", {
            palette: resolved,
            contrast: highContrast
              ? t("settings.contrastStateHigh")
              : t("settings.contrastStateNormal"),
            motion: reduceMotion
              ? t("settings.motionOff")
              : t("settings.motionOn"),
          })}
        </p>
      </section>

      <section
        aria-labelledby="a11y-keyboard-heading"
        className="space-y-3 rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="a11y-keyboard-heading" className="text-lg">
          {t("accessibility.keyboardHeading")}
        </h2>
        <ul className="list-disc space-y-2 pl-5 text-ink-muted">
          <li>{t("accessibility.keyboardSkip")}</li>
          <li>{t("accessibility.keyboardForms")}</li>
          <li>{t("accessibility.keyboardDialogs")}</li>
          <li>{t("accessibility.keyboardLiveRegions")}</li>
        </ul>
      </section>

      <section
        aria-labelledby="a11y-offline-heading"
        className="space-y-3 rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="a11y-offline-heading" className="text-lg">
          {t("accessibility.offlineHeading")}
        </h2>
        <p className="text-ink-muted">{t("accessibility.offlineBody")}</p>
      </section>

      <section
        aria-labelledby="a11y-limitations-heading"
        className="space-y-3 rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="a11y-limitations-heading" className="text-lg">
          {t("accessibility.limitationsHeading")}
        </h2>
        <p className="text-ink-muted">{t("accessibility.limitationsBody")}</p>
        <p className="text-sm text-ink-muted">
          {t("accessibility.lastReviewed")}
        </p>
        <div className="pt-2">
          <Link
            to="/settings"
            className="btn-secondary"
            data-testid="accessibility-to-settings"
          >
            {t("accessibility.backToSettings")}
          </Link>
        </div>
      </section>
    </div>
  );
}
