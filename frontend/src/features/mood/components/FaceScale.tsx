/**
 * The five faces, as one radio group.
 *
 * Native `<input type="radio">` elements on purpose: arrow-key navigation inside a
 * group, roving focus, "2 of 5" announced by screen readers, form semantics — all
 * of it comes from the platform, and all of it is what a hand-rolled div widget
 * gets wrong. The input is visually hidden and its *label* carries the focus ring
 * (`peer-focus-visible`, which is why the input is a sibling and not a child), so
 * keyboard users can still see where they are.
 *
 * Every face has its word next to it, in text: the scale is never colour-only and
 * never face-only.
 */

import type { ReactElement, Ref } from "react";

import { VALENCE_POINTS } from "../taxonomy";
import { MoodFace } from "./MoodFace";

export interface FaceScaleProps {
  /** `null` until a face is chosen: no default answer for "how do you feel". */
  value: number | null;
  onChange: (value: number) => void;
  legend: string;
  hint: string;
  /** Used for the ids the inputs point at, so a page can have more than one. */
  name?: string;
  invalid?: boolean;
  /**
   * The first radio, so a validation message can put focus on the group the way a
   * browser would for a required field.
   */
  firstInputRef?: Ref<HTMLInputElement>;
}

export function FaceScale({
  value,
  onChange,
  legend,
  hint,
  name = "mood-valence",
  invalid = false,
  firstInputRef,
}: FaceScaleProps): ReactElement {
  const hintId = `${name}-hint`;

  return (
    <fieldset
      className="border-0 p-0"
      data-testid="mood-face-scale"
      aria-invalid={invalid || undefined}
    >
      <legend className="text-base font-semibold">{legend}</legend>
      <p id={hintId} className="mt-1 text-sm text-ink-muted">
        {hint}
      </p>
      <div className="mt-3 grid grid-cols-5 gap-2">
        {VALENCE_POINTS.map((point) => {
          const selected = value === point.value;
          const inputId = `${name}-${point.value}`;
          return (
            <div key={point.value} className="flex flex-col">
              <input
                ref={point.value === 1 ? firstInputRef : undefined}
                type="radio"
                id={inputId}
                name={name}
                value={point.value}
                checked={selected}
                aria-describedby={hintId}
                onChange={() => {
                  onChange(point.value);
                }}
                className="peer sr-only"
              />
              <label
                htmlFor={inputId}
                data-testid={`mood-face-${point.value}`}
                data-selected={selected ? "true" : "false"}
                className={`flex h-full cursor-pointer flex-col items-center gap-1 rounded-xl border px-1 py-3 text-center peer-focus-visible:ring-2 peer-focus-visible:ring-focus ${
                  selected
                    ? "border-accent bg-accent-soft text-ink"
                    : "border-border bg-surface text-ink-muted hover:border-border-strong"
                }`}
              >
                <MoodFace shape={point.face} />
                <span className="text-sm leading-tight">{point.label}</span>
              </label>
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}
