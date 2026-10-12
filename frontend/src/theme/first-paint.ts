/**
 * Palette resolution before the app renders.
 *
 * This is the same logic as {@link ThemeProvider}, extracted so it can run as
 * the first module on the page instead of as an inline `<script>`. The
 * extraction is a security decision: an inline script (or an inline event
 * handler) is exactly what a strict `script-src 'self'` Content-Security-Policy
 * forbids, and loosening the policy to `'unsafe-inline'` so the palette does
 * not flash would trade a real defence for a cosmetic one.
 *
 * What flash risk is left: `src/index.css` already carries
 * `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) … }`,
 * so a reader who has *not* made an explicit choice is painted correctly with
 * no script at all. Only an explicit choice that disagrees with the operating
 * system can flash between parsing and this module running — the standard
 * trade-off, and the reason the CSS fallback exists.
 */

export { CONTRAST_STORAGE_KEY, THEME_STORAGE_KEY } from "./theme-storage";

import { CONTRAST_STORAGE_KEY, THEME_STORAGE_KEY } from "./theme-storage";

const DARK_QUERY = "(prefers-color-scheme: dark)";
const MORE_CONTRAST_QUERY = "(prefers-contrast: more)";

/**
 * Resolve and apply the palette on `<html>`, then return what was applied.
 *
 * Never throws: an unreadable `localStorage` (private mode, blocked origin)
 * means "follow the system", not a broken page. The function is exported so the
 * tests can call it directly with a stubbed `window`.
 */
export function applyFirstPaintTheme(): "light" | "dark" {
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    stored = null;
  }

  const systemPrefersDark =
    typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;

  const theme: "light" | "dark" =
    stored === "light" || stored === "dark" ? stored : systemPrefersDark ? "dark" : "light";

  document.documentElement.setAttribute("data-theme", theme);
  return theme;
}

/**
 * Day 25: the contrast level, resolved the same way — an explicit choice in
 * Settings wins, otherwise the operating system's `prefers-contrast: more`.
 * Writes `data-contrast="more"` (the high-contrast token set in `index.css`) or
 * removes the attribute. Never throws.
 */
export function applyFirstPaintContrast(): "more" | "standard" {
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(CONTRAST_STORAGE_KEY);
  } catch {
    stored = null;
  }
  const systemMore =
    typeof window.matchMedia === "function" && window.matchMedia(MORE_CONTRAST_QUERY).matches;
  const contrast: "more" | "standard" =
    stored === "more" || stored === "standard" ? stored : systemMore ? "more" : "standard";
  if (contrast === "more") {
    document.documentElement.setAttribute("data-contrast", "more");
  } else {
    document.documentElement.removeAttribute("data-contrast");
  }
  return contrast;
}

applyFirstPaintTheme();
applyFirstPaintContrast();
