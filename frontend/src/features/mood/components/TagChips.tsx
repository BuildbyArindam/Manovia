/**
 * Tag chips: checkboxes that look like chips and behave like checkboxes.
 *
 * The cap (three emotions, five factors) is enforced by *disabling* the chips
 * that would exceed it, plus a visible, announced line explaining why — a chip
 * that silently ignores a tap teaches people the app is broken. Nothing is ever
 * auto-evicted to make room: the app does not get to decide which of somebody's
 * feelings to drop.
 *
 * Order is preserved, because the first chip somebody taps is usually the one that
 * matters most, and the note under the chips says so.
 */

import type { ReactElement } from "react";

import type { EmotionOption } from "../taxonomy";

export interface TagChipsProps {
  options: readonly EmotionOption[];
  selected: readonly string[];
  onChange: (next: string[]) => void;
  legend: string;
  hint: string;
  /** The sentence shown when the cap is reached. */
  fullHint: string;
  max: number;
  name: string;
}

export function TagChips({
  options,
  selected,
  onChange,
  legend,
  hint,
  fullHint,
  max,
  name,
}: TagChipsProps): ReactElement {
  const atCap = selected.length >= max;

  function toggle(id: string, checked: boolean): void {
    if (checked) {
      if (selected.length >= max) {
        return; // the chip is disabled; this is belt and braces
      }
      onChange([...selected, id]);
      return;
    }
    onChange(selected.filter((value) => value !== id));
  }

  return (
    <fieldset className="border-0 p-0" data-testid={`chips-${name}`}>
      <legend className="text-base font-semibold">{legend}</legend>
      <p className="mt-1 text-sm text-ink-muted">{hint}</p>
      <ul className="mt-3 flex flex-wrap gap-2">
        {options.map((option) => {
          const checked = selected.includes(option.id);
          const disabled = atCap && !checked;
          const inputId = `${name}-${option.id}`;
          return (
            <li key={option.id}>
              <input
                type="checkbox"
                id={inputId}
                name={name}
                value={option.id}
                checked={checked}
                disabled={disabled}
                onChange={(event) => {
                  toggle(option.id, event.target.checked);
                }}
                className="peer sr-only"
              />
              <label
                htmlFor={inputId}
                data-testid={`chip-${option.id}`}
                data-selected={checked ? "true" : "false"}
                className={`inline-flex min-h-12 cursor-pointer items-center rounded-full border px-4 text-sm peer-focus-visible:ring-2 peer-focus-visible:ring-focus ${
                  checked
                    ? "border-accent bg-accent-soft font-semibold text-ink"
                    : "border-border bg-surface text-ink-muted hover:border-border-strong"
                } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
              >
                {option.label}
              </label>
            </li>
          );
        })}
      </ul>
      <p
        className="mt-2 text-sm text-ink-muted"
        aria-live="polite"
        data-testid={`chips-${name}-cap`}
      >
        {atCap ? fullHint : `${selected.length} of ${max} chosen`}
      </p>
    </fieldset>
  );
}
