import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { CONTRAST_STORAGE_KEY, THEME_STORAGE_KEY } from "./theme-storage";

/** Where the user's explicit palette and contrast choices are remembered. */
export { CONTRAST_STORAGE_KEY, THEME_STORAGE_KEY } from "./theme-storage";

const DARK_QUERY = "(prefers-color-scheme: dark)";
const MORE_CONTRAST_QUERY = "(prefers-contrast: more)";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";
export type ContrastPreference = "normal" | "high" | "system";

interface ThemeContextValue {
  /** What the user asked for: a palette, or "follow my system". */
  preference: ThemePreference;
  /** What is actually painted right now. */
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  /** What the user asked for on contrast: normal, high, or follow system (`prefers-contrast: more`). */
  contrastPreference: ContrastPreference;
  /** True when high-contrast tokens (`data-contrast="high"`) are active. */
  highContrast: boolean;
  setContrastPreference: (contrast: ContrastPreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStoredPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

function readStoredContrast(): ContrastPreference {
  try {
    const stored = window.localStorage.getItem(CONTRAST_STORAGE_KEY);
    return stored === "high" || stored === "normal" ? stored : "system";
  } catch {
    return "system";
  }
}

function systemPrefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

function systemPrefersMoreContrast(): boolean {
  if (typeof window.matchMedia !== "function") {
    return false;
  }
  const mql = window.matchMedia(MORE_CONTRAST_QUERY);
  return Boolean(mql && mql.media === MORE_CONTRAST_QUERY && mql.matches);
}

/**
 * Palette & contrast state.
 *
 * The attributes this writes (`data-theme` and `data-contrast` on `<html>`) are
 * what the CSS custom properties in `src/index.css` switch on, so the whole app
 * re-themes without a single conditional class. `index.html` carries a tiny
 * module (`first-paint.ts`) with this logic so the first painted frame is
 * already correct.
 */
export function ThemeProvider({ children }: { children: ReactNode }): ReactElement {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference);
  const [systemDark, setSystemDark] = useState<boolean>(systemPrefersDark);
  const [contrastPreference, setContrastState] =
    useState<ContrastPreference>(readStoredContrast);
  const [systemHighContrast, setSystemHighContrast] = useState<boolean>(
    systemPrefersMoreContrast,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const list = window.matchMedia(DARK_QUERY);
    const update = (): void => setSystemDark(list.matches);
    update();
    list.addEventListener("change", update);
    return () => {
      list.removeEventListener("change", update);
    };
  }, []);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const list = window.matchMedia(MORE_CONTRAST_QUERY);
    if (!list || typeof list.addEventListener !== "function") {
      return;
    }
    const update = (): void =>
      setSystemHighContrast(list.media === MORE_CONTRAST_QUERY && list.matches);
    update();
    list.addEventListener("change", update);
    return () => {
      list.removeEventListener("change", update);
    };
  }, []);

  const resolved: ResolvedTheme =
    preference === "system" ? (systemDark ? "dark" : "light") : preference;

  const highContrast: boolean =
    contrastPreference === "system" ? systemHighContrast : contrastPreference === "high";

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", resolved);
  }, [resolved]);

  useEffect(() => {
    if (highContrast) {
      document.documentElement.setAttribute("data-contrast", "high");
    } else {
      document.documentElement.removeAttribute("data-contrast");
    }
  }, [highContrast]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      if (next === "system") {
        window.localStorage.removeItem(THEME_STORAGE_KEY);
      } else {
        window.localStorage.setItem(THEME_STORAGE_KEY, next);
      }
    } catch {
      // A blocked storage must not break the switcher for this session.
    }
  }, []);

  const setContrastPreference = useCallback((next: ContrastPreference) => {
    setContrastState(next);
    try {
      if (next === "system") {
        window.localStorage.removeItem(CONTRAST_STORAGE_KEY);
      } else {
        window.localStorage.setItem(CONTRAST_STORAGE_KEY, next);
      }
    } catch {
      // A blocked storage must not break the switcher for this session.
    }
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({
      preference,
      resolved,
      setPreference,
      contrastPreference,
      highContrast,
      setContrastPreference,
    }),
    [
      preference,
      resolved,
      setPreference,
      contrastPreference,
      highContrast,
      setContrastPreference,
    ],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (context === null) {
    throw new Error("useTheme must be used inside a <ThemeProvider>");
  }
  return context;
}
