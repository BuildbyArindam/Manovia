/**
 * Small wrappers that remove the loud parts of a screen when they are not
 * helping (Day 46).
 *
 * Overwhelmed mode must hide *charts and badges* — not merely dim them. Hiding
 * at render time is deliberate: a chart that is fetched, measured and then
 * hidden with CSS still costs battery, and a badge inside a live region can
 * still be announced. The `data-sensory` attributes remain as a second line of
 * defence for markup rendered outside React (and make the rule legible in the
 * DOM for anyone inspecting it).
 *
 * `SensorySecondary` follows density instead: "simple" drops the explanatory
 * second sentence, the hint under a control, the decorative count. Headings and
 * the primary control are never wrapped in it.
 */

import type { ReactElement, ReactNode } from "react";

import { useSensory } from "./SensoryProvider";

export function SensoryChart({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): ReactElement | null {
  const { overwhelmed } = useSensory();
  if (overwhelmed) {
    return null;
  }
  return (
    <div data-sensory="chart" className={className}>
      {children}
    </div>
  );
}

export function SensoryBadge({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): ReactElement | null {
  const { overwhelmed } = useSensory();
  if (overwhelmed) {
    return null;
  }
  return (
    <span data-sensory="badge" className={className}>
      {children}
    </span>
  );
}

export function SensorySecondary({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): ReactElement | null {
  const { density } = useSensory();
  if (density === "simple") {
    return null;
  }
  return (
    <div data-sensory="secondary" className={className}>
      {children}
    </div>
  );
}
