import { useMediaQuery } from "@/lib/useMediaQuery";

/** The media query that asks the OS to reduce animation. */
export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

/**
 * True when the user asked for less motion.
 *
 * The CSS in `src/index.css` already collapses every animation and transition
 * under this query; this hook is for the cases CSS cannot reach — for example
 * deciding not to start a JS-driven transition at all, or announcing a step
 * change without a slide. It also gives the behaviour a test.
 */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery(REDUCED_MOTION_QUERY);
}
