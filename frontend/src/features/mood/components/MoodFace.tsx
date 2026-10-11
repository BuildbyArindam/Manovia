/**
 * One face on the valence scale — where the *shape* carries the meaning.
 *
 * AGENTS.md forbids colour-only communication, and a mood scale is the classic
 * place it goes wrong: five coloured circles and a "1-5" label, unusable to
 * somebody who cannot separate the colours. Here the mouth curve is the signal —
 * a frown, a flatter line, a wide smile — the colour is decoration on top, and
 * the word label sits under every face.
 *
 * Decorative by design: `aria-hidden`, because the radio it sits inside already
 * has an accessible name ("Very low") and a face read aloud adds nothing.
 */

import type { ReactElement } from "react";

export interface MoodFaceProps {
  /** The mouth path, in a 24x24 viewBox where the face is centred on (12, 12). */
  shape: string;
}

export function MoodFace({ shape }: MoodFaceProps): ReactElement {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      className="h-9 w-9"
      data-testid="mood-face"
    >
      <circle cx="12" cy="12" r="10.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="8.5" cy="9.5" r="1.3" fill="currentColor" />
      <circle cx="15.5" cy="9.5" r="1.3" fill="currentColor" />
      <path d={shape} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
