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
 * Formatting goes through `toLocaleDateString(undefined, …)`, i.e. the browser's
 * own language. Axis ticks and table cells therefore follow the reader's locale
 * (Devanagari digits for a Hindi browser) without this codebase owning a numeral
 * table; the *sentences* are localised by the server, which does own one.
 */

/** `2026-03-15` → `15 Mar 2026`, in the reader's own locale. */
export function formatLocalDay(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  if (!year || !month || !day) {
    return isoDate;
  }
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(undefined, {
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
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}
