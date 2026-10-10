/**
 * Suggested starters.
 *
 * Three generic sentences, shown only while the conversation is empty. They are
 * deliberately not diagnostic ("I feel anxious", not "I think I have anxiety")
 * and deliberately not dark: a starter chip is a way to begin, not a suggestion
 * about what the person must be feeling.
 *
 * A chip sends immediately rather than filling the box: the point is to remove
 * the blank-page problem, and a keyboard user reaches them with one Tab and one
 * Enter.
 */

import type { ReactElement } from "react";

import { CHAT_COPY } from "./copy";

export interface StarterChipsProps {
  onSelect: (text: string) => void;
  disabled?: boolean;
}

export function StarterChips({ onSelect, disabled = false }: StarterChipsProps): ReactElement {
  return (
    <section aria-labelledby="chat-starters-heading" data-testid="starter-chips">
      <h2 id="chat-starters-heading" className="text-sm font-semibold text-ink-muted">
        Not sure where to start?
      </h2>
      <ul className="mt-2 flex flex-wrap gap-2">
        {CHAT_COPY.starters.map((starter) => (
          <li key={starter}>
            <button
              type="button"
              data-testid="starter-chip"
              disabled={disabled}
              onClick={() => {
                onSelect(starter);
              }}
              className="inline-flex min-h-12 items-center rounded-full border border-border-strong bg-surface px-4 py-2 text-sm font-semibold disabled:opacity-50"
            >
              {starter}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
