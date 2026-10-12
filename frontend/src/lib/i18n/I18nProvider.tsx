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

const I18nContext = createContext<I18nState>({ language: "en", pseudo: false, revision: 0 });

export function I18nProvider({ children }: { children: ReactNode }): ReactElement {
  const [revision, bump] = useReducer((value: number) => value + 1, 0);

  useEffect(() => {
    const handleChange = (): void => {
      bump();
    };
    i18n.on("languageChanged", handleChange);
    return () => {
      i18n.off("languageChanged", handleChange);
    };
  }, []);

  return (
    <I18nContext.Provider
      value={{ language: currentAppLanguage(), pseudo: isPseudoMode(), revision }}
    >
      {children}
    </I18nContext.Provider>
  );
}

export function useI18nState(): I18nState {
  return useContext(I18nContext);
}
