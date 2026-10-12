/**
 * Keeps the tree re-rendering when the language changes.
 *
 * Most of the app reads its words through getters that call i18next at render
 * time (see the `copy.ts` modules), which means the strings are only as fresh
 * as the last render. This provider subscribes to i18next's
 * `languageChanged` event and bumps its own state, which re-renders the whole
 * subtree — and therefore re-reads every getter. Switching language is a rare,
 * deliberate act, so a full pass is the right price for never showing a mixed
 * screen.
 */

import {
  createContext,
  useContext,
  useEffect,
  useReducer,
  type ReactElement,
  type ReactNode,
} from "react";

import { currentAppLanguage, i18n, isPseudoMode, type AppLanguage } from "./i18n";

export interface I18nState {
  /** The interface language actually in force (pseudo reports "en"). */
  language: AppLanguage;
  /** True while the pseudo-localisation view is active. */
  pseudo: boolean;
  /** Changes on every language switch; lets memoised consumers re-render. */
  revision: number;
}

const INDIC_FONT_LINK_ID = "manovia-indic-fonts";
const INDIC_FONT_HREF =
  "https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;500;600;700&family=Noto+Sans+Devanagari:wght@400;500;600;700&display=swap";

function ensureIndicFontStylesheet(language: AppLanguage): void {
  if (
    (language !== "hi" && language !== "bn") ||
    typeof document === "undefined" ||
    import.meta.env.MODE === "test" ||
    (typeof navigator !== "undefined" && navigator.onLine === false)
  ) {
    return;
  }
  if (document.getElementById(INDIC_FONT_LINK_ID) !== null) {
    return;
  }
  const link = document.createElement("link");
  link.id = INDIC_FONT_LINK_ID;
  link.rel = "stylesheet";
  link.href = INDIC_FONT_HREF;
  document.head.appendChild(link);
}

const I18nContext = createContext<I18nState>({ language: "en", pseudo: false, revision: 0 });

export function I18nProvider({ children }: { children: ReactNode }): ReactElement {
  const [revision, bump] = useReducer((value: number) => value + 1, 0);
  const language = currentAppLanguage();

  useEffect(() => {
    const handleChange = (): void => {
      bump();
    };
    i18n.on("languageChanged", handleChange);
    return () => {
      i18n.off("languageChanged", handleChange);
    };
  }, []);

  useEffect(() => {
    ensureIndicFontStylesheet(language);
  }, [language]);

  return (
    <I18nContext.Provider
      value={{ language, pseudo: isPseudoMode(), revision }}
    >
      {children}
    </I18nContext.Provider>
  );
}

export function useI18nState(): I18nState {
  return useContext(I18nContext);
}
