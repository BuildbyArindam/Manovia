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

/** Where the user's explicit palette choice is remembered. */
export { CONTRAST_STORAGE_KEY, THEME_STORAGE_KEY } from "./theme-storage";

const DARK_QUERY = "(prefers-color-scheme: dark)";
const MORE_CONTRAST_QUERY = "(prefers-contrast: more)";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";
/** Day 25: the contrast choice, with the same "follow my device" option. */
export type ContrastPreference = "standard" | "more" | "system";
export type ResolvedContrast = "standard" | "more";

interface ThemeContextValue {
  /** What the user asked for: a palette, or "follow my system". */
  preference: ThemePreference;
  /** What is actually painted right now. */
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  /** Day 25: high contrast — asked for, and actually applied. */
  contrast: ContrastPreference;
  resolvedContrast: ResolvedContrast;
  setContrast: (contrast: ContrastPreference) => void;
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

function systemPrefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

function readStoredContrast(): ContrastPreference {
  try {
    const stored = window.localStorage.getItem(CONTRAST_STORAGE_KEY);
    return stored === "more" || stored === "standard" ? stored : "system";
  } catch {
    return "system";
  }
}

function systemPrefersMoreContrast(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(MORE_CONTRAST_QUERY).matches;
}

/** Subscribe to one media query, keeping the latest answer in state. */
function useMediaQueryState(query: string, initial: () => boolean): boolean {
  const [matches, setMatches] = useState<boolean>(initial);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const list = window.matchMedia(query);
    const update = (): void => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => {
      list.removeEventListener("change", update);
    };
  }, [query]);
  return matches;
}

/**
 * Palette state.
 *
 * The attribute this writes (`data-theme` on `<html>`) is what the CSS custom
 * properties in `src/index.css` switch on, so the whole app re-themes without a
 * single conditional class. `index.html` carries a tiny copy of this logic so
 * the first painted frame is already correct.
 */
export function ThemeProvider({ children }: { children: ReactNode }): ReactElement {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference);
  const [systemDark, setSystemDark] = useState<boolean>(systemPrefersDark);

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

  const resolved: ResolvedTheme =
    preference === "system" ? (systemDark ? "dark" : "light") : preference;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", resolved);
  }, [resolved]);

  const [contrast, setContrastState] = useState<ContrastPreference>(readStoredContrast);
  const systemMore = useMediaQueryState(MORE_CONTRAST_QUERY, systemPrefersMoreContrast);
  const resolvedContrast: ResolvedContrast =
    contrast === "system" ? (systemMore ? "more" : "standard") : contrast;

  useEffect(() => {
    if (resolvedContrast === "more") {
      document.documentElement.setAttribute("data-contrast", "more");
    } else {
      document.documentElement.removeAttribute("data-contrast");
    }
  }, [resolvedContrast]);

  const setContrast = useCallback((next: ContrastPreference) => {
    setContrastState(next);
    try {
      if (next === "system") {
        window.localStorage.removeItem(CONTRAST_STORAGE_KEY);
      } else {
        window.localStorage.setItem(CONTRAST_STORAGE_KEY, next);
      }
    } catch {
      // A blocked storage must not break the switch for this session.
    }
  }, []);

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

  const value = useMemo<ThemeContextValue>(
    () => ({ preference, resolved, setPreference, contrast, resolvedContrast, setContrast }),
    [preference, resolved, setPreference, contrast, resolvedContrast, setContrast],
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
