/**
 * The daily reminder preference — stored, and honestly labelled as not yet sent.
 *
 * The brief for Day 15 says "daily reminder preference stored (no push yet)", and
 * the only two defensible ways to ship that are (a) a switch that says nothing is
 * sent, or (b) no switch at all. This is (a): the value is kept on the device,
 * the copy says out loud that no reminder will arrive, and nobody is given a
 * false expectation about a product nudging them to check in.
 *
 * Nothing about it goes to the server: when somebody wants to be reminded to look
 * at their own feelings is exactly the kind of detail this product does not need
 * to store, and the setting works the moment reminders exist.
 */

import type { ReactElement } from "react";

import { MOOD_COPY } from "../copy";
import { isValidReminderTime } from "../storage";
import { useMoodReminder } from "../useMood";

export function ReminderPreference(): ReactElement {
  const [reminder, setReminder] = useMoodReminder();
  const timeInvalid = !isValidReminderTime(reminder.time);

  return (
    <section
      aria-labelledby="mood-reminder-heading"
      data-testid="mood-reminder"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id="mood-reminder-heading" className="text-lg">
        {MOOD_COPY.reminderHeading}
      </h2>
      <p className="mt-2 text-ink-muted">{MOOD_COPY.reminderBody}</p>

      <div className="mt-4 flex items-center gap-3">
        <input
          type="checkbox"
          id="mood-reminder-enabled"
          checked={reminder.enabled}
          onChange={(event) => {
            setReminder({ ...reminder, enabled: event.target.checked });
          }}
          className="h-6 w-6 accent-accent"
        />
        <label htmlFor="mood-reminder-enabled" className="text-base">
          {MOOD_COPY.reminderToggle}
        </label>
      </div>

      <div className="mt-3 flex items-center gap-3">
        <label htmlFor="mood-reminder-time" className="text-base">
          {MOOD_COPY.reminderTimeLabel}
        </label>
        <input
          type="time"
          id="mood-reminder-time"
          value={reminder.time}
          onChange={(event) => {
            setReminder({ ...reminder, time: event.target.value });
          }}
          className="min-h-12 rounded-xl border border-border bg-bg px-3 text-base text-ink"
        />
      </div>

      <p
        className={`mt-3 text-sm ${timeInvalid ? "text-danger" : "text-ink-muted"}`}
        role={timeInvalid ? "alert" : undefined}
        data-testid="mood-reminder-status"
      >
        {timeInvalid ? MOOD_COPY.reminderTimeInvalid : MOOD_COPY.reminderSaved}
      </p>
    </section>
  );
}
