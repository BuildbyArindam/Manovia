/**
 * The questionnaire: one fieldset per item, four options each, nothing typed.
 *
 * Accessibility decisions that matter more here than anywhere else in the app:
 *
 * * **A real `<fieldset>` with a real `<legend>`.** The question text is the
 *   group's label, so a screen reader announces "Question 3. Trouble falling or
 *   staying asleep — 0 of 4 selected" rather than reading four bare radios.
 * * **Native radios, no ARIA imitation.** Arrow keys move within a group and Tab
 *   moves to the next question for free, on every platform and every assistive
 *   technology — which is exactly what the keyboard-only test walks.
 * * **A failed submit explains itself and moves focus.** The error is a live
 *   region listing the unanswered question numbers, and focus lands on the first
 *   unanswered question's legend so the person is where the work is.
 * * **No score preview.** Nothing on this screen hints at a running total, so
 *   nobody answers "to get a number" — and no mid-band colouring can appear
 *   before the result exists.
 */

import { useEffect, useRef, type ReactElement } from "react";

import { INCOMPLETE_ERROR_TITLE, SUBMIT_HINT, answerGroupLabel } from "../copy";
import type { AssessmentDefinition } from "../types";

export interface QuestionnaireFormProps {
  definition: AssessmentDefinition;
  answers: Readonly<Record<string, number>>;
  onChange: (itemId: string, value: number) => void;
  onSubmit: () => void;
  busy: boolean;
  /** Set when the last submit was refused for missing answers. */
  unanswered: readonly string[];
  error: string | null;
}

export function QuestionnaireForm({
  definition,
  answers,
  onChange,
  onSubmit,
  busy,
  unanswered,
  error,
}: QuestionnaireFormProps): ReactElement {
  const legends = useRef(new Map<string, HTMLLegendElement>());

  useEffect(() => {
    const first = unanswered.find((itemId) => legends.current.has(itemId)) ?? null;
    if (first !== null) {
      legends.current.get(first)?.focus();
    }
  }, [unanswered]);

  return (
    <form
      data-testid="questionnaire"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <p className="text-lg text-ink" id="questionnaire-intro">
        {definition.intro}
      </p>

      {error !== null ? (
        <div
          role="alert"
          data-testid="questionnaire-error"
          className="mt-4 rounded-xl border border-border-strong bg-surface-muted p-4"
        >
          <p className="font-semibold">{INCOMPLETE_ERROR_TITLE}</p>
          <p className="mt-1 text-ink-muted">{error}</p>
        </div>
      ) : null}

      <ol className="mt-6 space-y-4">
        {definition.items.map((item) => {
          const answered = answers[item.id];
          const missing = unanswered.includes(item.id);
          return (
            <li key={item.id}>
              <fieldset
                id={`item-${item.id}`}
                data-testid={`item-${item.id}`}
                className={`rounded-2xl border bg-surface p-4 sm:p-6 ${
                  missing ? "border-border-strong" : "border-border"
                }`}
              >
                <legend
                  tabIndex={-1}
                  ref={(node) => {
                    if (node === null) {
                      legends.current.delete(item.id);
                    } else {
                      legends.current.set(item.id, node);
                    }
                  }}
                  className="max-w-prose px-1 text-base font-medium"
                >
                  <span className="sr-only">{answerGroupLabel(item.position, item.text)}</span>
                  <span aria-hidden="true">
                    {item.position}. {item.text}
                  </span>
                </legend>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {definition.response_options.map((option) => (
                    <label
                      key={option.value}
                      className={`flex min-h-12 cursor-pointer items-center gap-3 rounded-xl border px-4 py-3 ${
                        answered === option.value
                          ? "border-accent bg-accent-soft"
                          : "border-border bg-surface"
                      }`}
                    >
                      <input
                        type="radio"
                        name={item.id}
                        value={option.value}
                        checked={answered === option.value}
                        onChange={() => {
                          onChange(item.id, option.value);
                        }}
                        className="h-5 w-5"
                      />
                      <span>
                        <span className="block">{option.label}</span>
                        {option.detail !== null ? (
                          <span className="block text-sm text-ink-muted">{option.detail}</span>
                        ) : null}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            </li>
          );
        })}
      </ol>

      <p className="mt-6 text-sm text-ink-muted">{SUBMIT_HINT}</p>
      <button
        type="submit"
        disabled={busy}
        className="mt-4 inline-flex min-h-12 items-center justify-center rounded-xl bg-accent px-5 py-3 font-semibold text-accent-fg disabled:opacity-60"
      >
        {busy ? "Saving…" : "See my result"}
      </button>
    </form>
  );
}
