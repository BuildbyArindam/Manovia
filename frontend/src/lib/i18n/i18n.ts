/**
 * The app's language layer (Day 23): i18next, initialised synchronously with
 * bundled resources, so the very first render is already in the right language
 * and no test needs to await an init promise.
 *
 * Resolution order, and why:
 *
 * 1. The saved choice (`localStorage`): language is a preference, not a
 *    geolocation guess, and it must survive a reload.
 * 2. The browser's language, matched by primary subtag (`hi-IN` → `hi`):
 *    a first-time Hindi browser should meet Hindi copy, not English.
 * 3. English — the fallback every other surface in the product uses.
 *
 * Pseudo-localisation is a fourth, development-only state: the pseudo flag in
 * localStorage overrides the language with `pseudo`, which renders English
 * strings through the accent/expansion post-processor. It never reaches a real
 * reader — see `pseudo.ts`.
 *
 * The *server's* language (crisis copy, insight sentences, the plan) is a
 * separate decision the API surfaces make per request; the chat sends this
 * language along as `locale`, and the plan and insights endpoints take it as
 * a query parameter, so switching here switches those too.
 */

import i18next from "i18next";

import bn from "./locales/bn.json";
import en from "./locales/en.json";
import hi from "./locales/hi.json";
import { pseudoPostProcessor } from "./pseudo";

/** The languages the interface ships, in switcher order. */
export const APP_LANGUAGES = [
  { code: "en", native: "English", english: "English" },
  { code: "hi", native: "हिन्दी", english: "Hindi" },
  { code: "bn", native: "বাংলা", english: "Bengali" },
] as const;

export type AppLanguage = (typeof APP_LANGUAGES)[number]["code"];

export const LANGUAGE_STORAGE_KEY = "manovia.language.v1";
export const PSEUDO_STORAGE_KEY = "manovia.pseudo.v1";

/** The i18next language code pseudo mode runs under. */
export const PSEUDO_LANGUAGE = "pseudo";

export function isAppLanguage(value: string | null | undefined): value is AppLanguage {
  return value === "en" || value === "hi" || value === "bn";
}

function safeStorageGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageSet(key: string, value: string | null): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // Private mode or a blocked origin: the preference lives for this page
    // load only. That is a degraded preference, not a broken app.
  }
}

/** True when pseudo-localisation was asked for (?pseudo=1 persists). */
export function pseudoEnabled(): boolean {
  return safeStorageGet(PSEUDO_STORAGE_KEY) === "1";
}

/**
 * Read the `?pseudo=1` flag once, at startup, and persist it. `?pseudo=0`
 * clears it. Any other value leaves the stored flag alone, so a shared link
 * cannot silently reconfigure somebody's browser.
 */
export function adoptPseudoFlagFromUrl(): void {
  let flag: string | null = null;
  try {
    flag = new URLSearchParams(window.location.search).get("pseudo");
  } catch {
    return;
  }
  if (flag === "1") {
    safeStorageSet(PSEUDO_STORAGE_KEY, "1");
  } else if (flag === "0") {
    safeStorageSet(PSEUDO_STORAGE_KEY, null);
  }
}

function storedLanguage(): AppLanguage | null {
  const value = safeStorageGet(LANGUAGE_STORAGE_KEY);
  return isAppLanguage(value) ? value : null;
}

function browserLanguage(): AppLanguage | null {
  if (typeof navigator === "undefined") {
    return null;
  }
  const candidates = [navigator.language ?? "", ...(navigator.languages ?? [])];
  for (const candidate of candidates) {
    const primary = candidate.trim().toLowerCase().split("-")[0];
    if (isAppLanguage(primary)) {
      return primary;
    }
  }
  return null;
}

/** The language the app starts in, before anyone touches the switcher. */
export function initialLanguage(): AppLanguage {
  return storedLanguage() ?? browserLanguage() ?? "en";
}

adoptPseudoFlagFromUrl();

// init() resolves synchronously here: the resources are bundled and no backend
// plugin is registered, so there is nothing to load and `t` works from the
// very first line after this call.
void i18next.use(pseudoPostProcessor).init({
  resources: {
    en: { translation: en },
    hi: { translation: hi },
    bn: { translation: bn },
    // Pseudo renders the English tree through the post-processor.
    [PSEUDO_LANGUAGE]: { translation: en },
  },
  lng: pseudoEnabled() ? PSEUDO_LANGUAGE : initialLanguage(),
  fallbackLng: "en",
  // Registration via `use()` alone does not activate a post-processor; this
  // option does. The processor itself is a no-op for real locales, so running
  // it on every translation costs nothing and makes pseudo mode actually work.
  postProcess: [pseudoPostProcessor.name],
  // React already escapes; double-escaping would show &amp; to readers.
  interpolation: { escapeValue: false },
  returnNull: false,
});

/** The active i18next instance, for modules that translate outside components. */
export const i18n = i18next;

/** Translate through the module-level instance. */
export function t(key: string, options?: Record<string, unknown>): string {
  return i18next.t(key, options ?? {}) as string;
}

/** The interface language a reader actually chose (pseudo counts as English). */
export function currentAppLanguage(): AppLanguage {
  const language = i18next.language;
  return isAppLanguage(language) ? language : "en";
}

/**
 * The BCP-47 tag to hand to `Intl.DateTimeFormat` / `Intl.NumberFormat`.
 * Pseudo mode formats like English: it exaggerates layout, not numerals.
 */
export function intlLocale(): string {
  return currentAppLanguage();
}

/** True while the pseudo-localisation view is active. */
export function isPseudoMode(): boolean {
  return i18next.language === PSEUDO_LANGUAGE;
}

function applyDocumentLanguage(): void {
  if (typeof document !== "undefined") {
    // The lang attribute drives the Indic line-height and font stacks in
    // index.css, and tells screen readers which voice to use. Pseudo keeps
    // the underlying language: it is a rendering aid, not a tongue.
    document.documentElement.lang = currentAppLanguage();
  }
}

applyDocumentLanguage();

/**
 * Switch the interface language and persist it. Pseudo is entered through
 * :func:`setPseudoMode` because it must never overwrite the saved choice.
 */
export function setAppLanguage(language: AppLanguage): void {
  safeStorageSet(LANGUAGE_STORAGE_KEY, language);
  const target = isPseudoMode() ? PSEUDO_LANGUAGE : language;
  void i18next.changeLanguage(target);
  applyDocumentLanguage();
}

/** Turn pseudo-localisation on or off without losing the saved language. */
export function setPseudoMode(enabled: boolean): void {
  safeStorageSet(PSEUDO_STORAGE_KEY, enabled ? "1" : null);
  void i18next.changeLanguage(enabled ? PSEUDO_LANGUAGE : currentAppLanguage());
  applyDocumentLanguage();
}
