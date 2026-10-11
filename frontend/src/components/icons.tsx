/**
 * Inline navigation icons.
 *
 * Drawn with `currentColor` and a 1.75 stroke so they inherit the text colour
 * (and therefore the theme), stay crisp at 24px, and add no network request —
 * no icon font, no CDN, nothing to block on.
 */

import type { ReactElement } from "react";

export type IconName =
  "home" | "chat" | "mood" | "journal" | "exercises" | "assessments" | "insights" | "settings";

const PATHS: Record<IconName, ReactElement> = {
  home: (
    <>
      <path d="M4 10.5 12 4l8 6.5" />
      <path d="M6 9.5V20h12V9.5" />
      <path d="M10 20v-5h4v5" />
    </>
  ),
  chat: (
    <>
      <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7A2.5 2.5 0 0 1 17.5 16H10l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 13.5v-7Z" />
      <path d="M8.5 9.5h7" />
    </>
  ),
  mood: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M9 10h.01" />
      <path d="M15 10h.01" />
      <path d="M9 14.5c1 1 1.9 1.5 3 1.5s2-.5 3-1.5" />
    </>
  ),
  journal: (
    <>
      <path d="M6 4h10a2 2 0 0 1 2 2v14H8a2 2 0 0 1-2-2V4Z" />
      <path d="M9 8h6M9 12h6" />
      <path d="M6 4v16" />
    </>
  ),
  exercises: (
    <>
      <path d="M5 19c0-8 6-12 14-12 0 8-6 12-14 12Z" />
      <path d="M5 19c3-3.2 6-5.2 9-6.2" />
    </>
  ),
  assessments: (
    <>
      <path d="M7 4h10v3H7z" />
      <path d="M6 5.5H5.5A1.5 1.5 0 0 0 4 7v11.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V7a1.5 1.5 0 0 0-1.5-1.5H18" />
      <path d="M8.5 12.5l2 2 4.5-5" />
    </>
  ),
  insights: (
    <>
      <path d="M5 19V10" />
      <path d="M10 19V5" />
      <path d="M15 19v-6" />
      <path d="M20 19v-9" />
    </>
  ),
  settings: (
    <>
      <path d="M4 8h8M16 8h4M4 16h4M12 16h8" />
      <circle cx="14" cy="8" r="2.25" />
      <circle cx="10" cy="16" r="2.25" />
    </>
  ),
};

export function NavIcon({
  name,
  className = "h-6 w-6",
}: {
  name: IconName;
  className?: string;
}): ReactElement {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
