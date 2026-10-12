/**
 * Where the explicit palette choice is remembered — one definition, three
 * readers.
 *
 * This tiny module exists because of a real trap: `ThemeProvider` (which
 * writes the choice) and `first-paint` (which reads it before React mounts)
 * used to carry their *own* copies of the string. Each was individually
 * correct, every test stubbed storage through the constant it had imported,
 * and so nothing at all would have failed if one copy drifted — the app would
 * simply have lost the stored preference and flashed the system palette on
 * every load, which is exactly the bug the first-paint module exists to
 * prevent. A shared constant makes that state unrepresentable; the pinned
 * literal in `theme-storage.test.ts` makes changing it a deliberate act rather
 * than an accident.
 *
 * The module deliberately has no imports and no side effects: `first-paint` is
 * the very first module on the page, and it must not drag React in with it.
 */

/** `localStorage` key holding `"light" | "dark"` (absent means "follow system"). */
export const THEME_STORAGE_KEY = "manovia.theme";

/**
 * Day 25: `localStorage` key holding `"more" | "standard"` for the contrast
 * choice (absent means "match my device", i.e. `prefers-contrast: more`). Same
 * rule as the palette key: one definition, read by `first-paint` and written by
 * `ThemeProvider`.
 */
export const CONTRAST_STORAGE_KEY = "manovia.contrast";
