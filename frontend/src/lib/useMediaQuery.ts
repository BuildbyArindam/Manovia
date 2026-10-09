import { useEffect, useState } from "react";

/**
 * Subscribe to a CSS media query.
 *
 * `defaultValue` is what the first render reports (before the effect runs), so
 * callers that must not render the wrong layout pick a value that keeps the
 * first paint sane. Tests stub `window.matchMedia` to drive this.
 */
export function useMediaQuery(query: string, defaultValue = false): boolean {
  const [matches, setMatches] = useState(defaultValue);

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
