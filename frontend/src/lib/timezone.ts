/**
 * The one piece of browser knowledge the day-counting features need: the
 * device's own timezone.
 *
 * `Intl.DateTimeFormat().resolvedOptions().timeZone` is an IANA name
 * (`Asia/Kolkata`), which is exactly what the backend's streak and insights
 * arithmetic wants. Sending it matters: without it the server counts days in
 * UTC, and somebody checking in at 00:30 in Delhi would see their streak — and
 * their "parts of the day" pattern — disagree with their calendar. It is a
 * preference, not an identifier: the server stores no zone.
 *
 * This lives in `lib/` rather than inside a feature because two features need
 * the same fact (mood streaks since Day 15, insights since Day 16) and a fact
 * with two homes drifts. `features/mood/endpoints.ts` re-exports it so its
 * callers, and their tests, did not have to move.
 */

/** The device's IANA timezone, or `"UTC"` when the browser will not say. */
export function deviceTimezone(): string {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof zone === "string" && zone.length > 0 ? zone : "UTC";
  } catch {
    return "UTC";
  }
}
