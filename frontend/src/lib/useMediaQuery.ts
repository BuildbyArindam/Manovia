import { useEffect, useState } from "react";

/**
 * Subscribe to a CSS media query.
 *
 * `defaultValue` is what the first render reports (before the effect runs), so
 * callers that must not render the wrong layout pick a value that keeps the
 * first paint sane. Tests stub `window.matchMedia` to drive this.
 */
export function useMediaQuery(query: string, defaultValue = false): boolean {
  // Read the answer synchronously for the first paint. Subscribing in an
  // effect is still necessary (the OS setting can change), but starting from
  // `defaultValue` meant a reduced-motion reader saw one animated frame before
  // the correction — Day 46's calm space must not start moving at all.
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      try {
        return window.matchMedia(query).matches;
      } catch {
        return defaultValue;
      }
    }
    return defaultValue;
  });

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      setMatches(defaultValue);
      return;
    }
    const list = window.matchMedia(query);
    const update = (): void => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => {
      list.removeEventListener("change", update);
    };
  }, [query, defaultValue]);

  return matches;
}
