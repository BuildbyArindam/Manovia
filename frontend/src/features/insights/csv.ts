/**
 * CSV export: one pure builder, one guarded downloader.
 *
 * Splitting them is the whole design. `buildCsv` is a pure function of rows, so
 * the escaping rules — quotes, commas, newlines, the byte-order mark that stops
 * Excel from mangling Devanagari and Bengali labels — are tested directly, with
 * no DOM in the way. `downloadCsv` is the only part that touches the document,
 * and it reports whether it managed to, because a browser without
 * `URL.createObjectURL` (or a test environment) must produce a clear "no" rather
 * than a thrown exception from a button handler.
 *
 * The export is built on the client from the payload the client already has.
 * Nothing is sent anywhere and no new endpoint exists for it: an export feature
 * that made another request would be another place for the two copies of the
 * numbers to disagree.
 */

import type { DataTable } from "./series";

/**
 * Serialise a table to CSV (RFC 4180).
 *
 * Line endings are CRLF because that is what the format says and what Excel
 * expects; fields are quoted only when they must be, so the file stays readable
 * in a text editor.
 */
export function buildCsv(
  columns: readonly string[],
  rows: readonly (readonly (string | number | null)[])[],
): string {
  const escape = (value: string | number | null): string => {
    if (value === null) {
      return "";
    }
    const text = typeof value === "number" ? String(value) : value;
    return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };

  const lines = [columns.map(escape).join(","), ...rows.map((row) => row.map(escape).join(","))];
  return `${lines.join("\r\n")}\r\n`;
}

/**
 * A data table as CSV, with its reading note kept as a trailing comment line.
 *
 * The note is what tells somebody that an empty cell means "not enough data"
 * rather than "zero", and that matters more once the table has left the page and
 * is sitting in a spreadsheet with no interface around it.
 */
export function csvFromTable(table: DataTable): string {
  const rows = table.rows.map((row) => table.columns.map((column) => row[column.key] ?? null));
  const csv = buildCsv(
    table.columns.map((column) => column.label),
    rows,
  );
  return table.note === undefined ? csv : `${csv}\r\n${buildCsv([], [[`# ${table.note}`]])}`;
}

/** A filename that says what it is and which day it was made on. */
export function csvFilename(topic: string, today: string): string {
  const slug = topic
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `manovia-insights-${slug}-${today}.csv`;
}

/**
 * Offer `csv` to the browser as a download.
 *
 * Returns false when the environment cannot do it (no document, no object URLs)
 * so the caller can say so instead of failing silently. The byte-order mark at
 * the front is not decoration: without it Excel assumes a legacy code page and
 * the localised labels in these tables arrive as mojibake.
 */
export function downloadCsv(filename: string, csv: string): boolean {
  if (
    typeof document === "undefined" ||
    typeof URL === "undefined" ||
    typeof URL.createObjectURL !== "function" ||
    typeof Blob === "undefined"
  ) {
    return false;
  }

  const blob = new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();

  // Revoking on the next tick rather than immediately: some browsers cancel the
  // download if the object URL disappears before the save dialogue has taken it.
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 1000);
  return true;
}
