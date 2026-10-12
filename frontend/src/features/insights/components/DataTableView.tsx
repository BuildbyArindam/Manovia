/**
 * The data-table alternative every chart on this page has to have.
 *
 * A real `<table>`, with a `<caption>` that says what it is, `scope="col"` on
 * every header, numeric columns right-aligned and marked up as such, and an em
 * dash where the server had nothing to say. An empty cell would be read as zero
 * by anybody glancing at a spreadsheet-shaped thing, and zero is a claim.
 *
 * It is presentational: the rows come from `series.ts`, the same place the chart
 * rows come from, so the table and the picture cannot disagree.
 */

import { useId, type ReactElement } from "react";
import { t } from "@/lib/i18n/i18n";

import type { DataTable } from "../series";

export interface DataTableViewProps {
  table: DataTable;
  /** The id the surrounding disclosure is labelled by, if it has one. */
  id?: string;
}

export function DataTableView({ table, id }: DataTableViewProps): ReactElement {
  const noteId = useId();

  return (
    <div
      className="overflow-x-auto"
      data-testid="insight-data-table"
      tabIndex={0}
      role="region"
      aria-label={table.caption}
    >
      <table
        id={id}
        aria-describedby={table.note === undefined ? undefined : noteId}
        className="w-full min-w-[36rem] border-collapse text-sm"
      >
        <caption className="pb-3 text-left text-base text-ink-muted">{table.caption}</caption>
        <thead>
          <tr className="border-b border-border-strong">
            {table.columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={`py-2 pr-4 font-medium ${
                  column.numeric === true ? "text-right" : "text-left"
                }`}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.length === 0 ? (
            <tr>
              <td colSpan={table.columns.length} className="py-3 text-ink-muted">
                Nothing to show yet.
              </td>
            </tr>
          ) : (
            // Positional keys: these tables are read-only, never reordered or
            // filtered under a reader, and no column is unique enough to be an
            // identity (two weeks can both have zero check-ins).
            table.rows.map((row, index) => (
              <tr key={index} className="border-b border-border">
                {table.columns.map((column) => {
                  const value = row[column.key] ?? null;
                  return (
                    <td
                      key={column.key}
                      className={`py-2 pr-4 align-top ${
                        column.numeric === true ? "text-right tabular-nums" : "text-left"
                      }`}
                    >
                      {value === null ? <span aria-hidden="true">—</span> : value}
                      {value === null ? <span className="sr-only">{t("insights.noData")}</span> : null}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
      {table.note === undefined ? null : (
        /* Described-by rather than a caption or a footnote: the note tells a
           reader what an empty cell means, which is a property of the table and
           should arrive with it, not sit next to it unattached. */
        <p id={noteId} className="mt-3 max-w-prose text-sm text-ink-muted">
          {table.note}
        </p>
      )}
    </div>
  );
}
