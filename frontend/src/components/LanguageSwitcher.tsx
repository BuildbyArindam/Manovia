/**
 * The language picker: every interface language in its own script, plus the
 * pseudo-localisation entry when it is already on or this is a development
 * build — it is a QA tool, not a language a reader should ever be offered.
 *
 * The switch persists immediately (see `lib/i18n`): there is no Save button
 * between a person and reading the app in their own language.
 */

import { useId, type ChangeEvent, type ReactElement } from "react";

import {
  APP_LANGUAGES,
  setAppLanguage,
  setPseudoMode,
  t,
  PSEUDO_LANGUAGE,
  type AppLanguage,
} from "@/lib/i18n/i18n";
import { useI18nState } from "@/lib/i18n/I18nProvider";

export function LanguageSwitcher(): ReactElement {
  const { language, pseudo } = useI18nState();
  // Day 25: two ids. The label text and the select used to share one, which is
  // invalid HTML and made `aria-labelledby` point at an ambiguous target.
  const labelId = useId();
  const selectId = useId();
  const showPseudo = pseudo || import.meta.env.DEV;
  const value = pseudo ? PSEUDO_LANGUAGE : language;

  function handleChange(event: ChangeEvent<HTMLSelectElement>): void {
    const chosen = event.target.value;
    if (chosen === PSEUDO_LANGUAGE) {
      setPseudoMode(true);
    } else {
      setPseudoMode(false);
      setAppLanguage(chosen as AppLanguage);
    }
  }

  return (
    <label className="flex items-center gap-2 text-sm text-ink-muted" htmlFor={selectId}>
      {/* Visible on wide screens; on a 360px phone the select keeps the space. */}
      <span id={labelId} className="hidden sm:inline">
        {t("language.label")}
      </span>
      <select
        id={selectId}
        data-testid="language-switcher"
        aria-labelledby={labelId}
        value={value}
        onChange={handleChange}
        className="min-h-11 rounded-lg border border-border-strong bg-surface px-2 text-ink"
      >
        {APP_LANGUAGES.map((option) => (
          <option key={option.code} value={option.code} lang={option.code}>
            {option.native}
          </option>
        ))}
        {showPseudo ? (
          <option value={PSEUDO_LANGUAGE}>{t("language.pseudo")}</option>
        ) : null}
      </select>
    </label>
  );
}
