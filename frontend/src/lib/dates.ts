/**
 * Date formatting for dates the server has already put in the reader's timezone.
 *
 * Every date in an insights or mood payload is a plain `YYYY-MM-DD` string that
 * means *that calendar day where the reader is*. Parsing it with `new Date(str)`
 * would read it as midnight UTC and, in any zone behind UTC, show the day
 * before — the single most common way a localised feature quietly un-localises
 * itself. These helpers build the Date from its parts and pin `timeZone: "UTC"`
 * so the calendar day survives the trip.
 *
 * Formatting goes through `toLocaleDateString(intlLocale(), …)`: the language
 * the reader picked in the interface, not whatever the browser happens to say
 * (Day 23). Axis ticks and table cells therefore follow the app's language —
 * Devanagari digits for a Hindi reader, Bengali digits for a Bengali one — via
 * `Intl`, without this codebase owning a numeral table; the *sentences* are
 * localised by the server, which does own one.
 */

import { intlLocale } from "@/lib/i18n/i18n";

/** `2026-03-15` → `15 Mar 2026`, in the reader's own locale. */
export function formatLocalDay(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  if (!year || !month || !day) {
    return isoDate;
  }
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(intlLocale(), {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** `2026-03-15` → `15 Mar` — an axis tick, where the year is noise. */
export function formatShortDay(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  if (!year || !month || !day) {
    return isoDate;
  }
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(intlLocale(), {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

/**
 * An ISO timestamp from the server → a local date and time for the reader.
 *
 * Unlike the day helpers above, this one *wants* the reader's zone: a receipt
 * or an agreement was recorded at an instant, and the honest rendering of an
 * instant is in the clock the reader is actually looking at. An unparseable
 * value is returned unchanged rather than shown as "Invalid Date".
 */
export function formatDateTime(isoTimestamp: string): string {
  const parsed = new Date(isoTimestamp);
  if (Number.isNaN(parsed.getTime())) {
    return isoTimestamp;
  }
  return parsed.toLocaleString(intlLocale(), {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
