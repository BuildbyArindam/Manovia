/**
 * Energy: a real range input, with words.
 *
 * `<input type="range" min="1" max="5" step="1">` gives arrow-key stepping, Home
 * and End, and the correct role for free. Two things are added on top:
 *
 * * **A real `<label>`** — a lone slider in a fieldset is not enough for axe, and
 *   not enough for somebody tabbing blind into a number with no question attached.
 * * **`aria-valuetext`** — a screen reader should say "Running low", not "2", and
 *   definitely not "47 percent".
 * * **The five words, visible under the track**, with the current one marked by
 *   weight and shape. A slider with an unlabelled thumb is a guess; a slider whose
 *   only feedback is a number is a scoring exercise.
 */

import type { ReactElement } from "react";

import { ENERGY_POINTS, energyPoint, localisedEnergyLabel } from "../taxonomy";

export interface EnergyScaleProps {
  value: number;
  onChange: (value: number) => void;
  legend: string;
  hint: string;
  name?: string;
}

export function EnergyScale({
  value,
  onChange,
  legend,
  hint,
  name = "mood-energy",
}: EnergyScaleProps): ReactElement {
  const current = energyPoint(value);
  const hintId = `${name}-hint`;

  return (
    <div data-testid="mood-energy-scale">
      <label htmlFor={name} className="text-base font-semibold">
        {legend}
      </label>
      <p id={hintId} className="mt-1 text-sm text-ink-muted">
        {hint}
      </p>
      <div className="mt-3">
        <input
          type="range"
          id={name}
          name={name}
          min={1}
          max={5}
          step={1}
          value={value}
          aria-valuetext={localisedEnergyLabel(current)}
          aria-describedby={hintId}
          onChange={(event) => {
            onChange(Number(event.target.value));
          }}
          className="h-12 w-full cursor-pointer accent-accent"
        />
        <p className="text-base" data-testid="mood-energy-readout" aria-live="polite">
          <span className="font-semibold">{value}</span> — {localisedEnergyLabel(current)}
        </p>
        <ul className="mt-2 flex justify-between text-xs text-ink-muted" aria-hidden="true">
          {ENERGY_POINTS.map((point) => (
            <li
              key={point.value}
              data-current={point.value === value ? "true" : "false"}
              className={point.value === value ? "font-semibold text-ink" : undefined}
            >
              {localisedEnergyLabel(point)}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
