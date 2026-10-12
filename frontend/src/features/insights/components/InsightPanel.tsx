/**
 * One insight, as a reader meets it: a heading, sentences, a picture, a table.
 *
 * The order in this file is the accessibility design, not decoration:
 *
 * 1. **The sentences come first and are always rendered.** They are the server's
 *    reviewed copy, in the reader's language, and they say everything the chart
 *    shows — including "not enough data". A screen-reader user, a reader with
 *    the images blocked, and a reader who simply prefers words all get the whole
 *    insight without opening anything.
 * 2. **The chart is `aria-hidden`.** It adds nothing an assistive technology can
 *    use: its axes are pictures of numbers that are already in the sentences and
 *    the table. Hiding it is the honest option; giving it a one-line `aria-label`
 *    would be the dishonest one, because a label cannot carry 56 days of data.
 * 3. **The data table is in a disclosure, not behind a tab or a hover.** Same
 *    numbers as the chart, reachable by keyboard, and the button says what it
 *    will do. Closed by default because a page of eight tables is not a page
 *    anybody reads, and the table is an *alternative* to the picture rather than
 *    the only way to get the data.
 * 4. **The CSV button reports what happened** through a polite live region,
 *    because "download started" is invisible to anyone who cannot see the
 *    browser's own download shelf.
 *
 * The chart slot is optional on purpose: some insights (the trend, say) are two
 * numbers and a direction, and drawing them as bars would invite reading a gap
 * as larger than its confidence interval says it is.
 */

import { useId, useState, type ReactElement, type ReactNode } from "react";

import { INSIGHTS_COPY } from "../copy";
import { csvFilename, csvFromTable, downloadCsv } from "../csv";
import type { DataTable } from "../series";
import { DataTableView } from "./DataTableView";

export interface InsightPanelProps {
  heading: string;
  /** The server's sentences, in reading order. Never empty in practice. */
  summary: readonly string[];
  /** Anything else worth saying that is not a summary sentence. */
  note?: string;
  /** The picture. This component wraps it in `aria-hidden`. */
  chart?: ReactNode;
  /** The same numbers as the chart, as a table. Required whenever there is a chart. */
  table: DataTable;
  /** Filename topic for the CSV export (`manovia-insights-<topic>-<today>.csv`). */
  csvTopic: string;
  /** The response's own `today`, so the exported filename matches the data. */
  today: string;
  testId?: string;
}

export function InsightPanel({
  heading,
  summary,
  note,
  chart,
  table,
  csvTopic,
  today,
  testId,
}: InsightPanelProps): ReactElement {
  const headingId = useId();
  const tableId = useId();
  const buttonId = useId();
  const statusId = useId();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const exportCsv = (): void => {
    setStatus(
      downloadCsv(csvFilename(csvTopic, today), csvFromTable(table))
        ? INSIGHTS_COPY.downloadStarted
        : INSIGHTS_COPY.downloadBlocked,
    );
  };

  return (
    <section
      aria-labelledby={headingId}
      data-testid={testId ?? "insight-panel"}
      data-insight-panel="true"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h3 id={headingId} className="text-lg">
        {heading}
      </h3>

      {/* The text version of this insight. First, always present, never hidden. */}
      <div data-testid="insight-summary" className="mt-3 max-w-prose space-y-2">
        {summary.map((sentence) => (
          <p key={sentence}>{sentence}</p>
        ))}
        {note === undefined ? null : <p className="text-ink-muted">{note}</p>}
      </div>

      {chart === undefined ? null : (
        <div
          className="relative mt-4 overflow-x-auto"
          tabIndex={0}
          role="group"
          aria-label={INSIGHTS_COPY.chartRegion(heading)}
        >
          {/* Hidden from assistive technology as a whole: the sentences above and
              the table below carry the same numbers, and a chart cannot be
              summarised by a label without losing the data. */}
          <div aria-hidden="true" className="min-w-fit">
            {chart}
          </div>
        </div>
      )}

      {chart === undefined ? null : (
        <p className="mt-2 text-xs text-ink-muted">{INSIGHTS_COPY.chartDecorative}</p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          id={buttonId}
          aria-expanded={open}
          aria-controls={tableId}
          onClick={() => {
            setOpen((previous) => !previous);
          }}
          className="rounded-lg border border-border-strong px-3 py-2 text-sm underline-offset-2 hover:bg-surface-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          {open ? INSIGHTS_COPY.hideTable : INSIGHTS_COPY.showTable}
        </button>

        <button
          type="button"
          onClick={exportCsv}
          aria-describedby={statusId}
          className="rounded-lg border border-border-strong px-3 py-2 text-sm underline-offset-2 hover:bg-surface-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          {INSIGHTS_COPY.downloadCsv}
        </button>
      </div>

      {/* Announced, not assumed: a download is otherwise invisible here. */}
      <p
        id={statusId}
        aria-live="polite"
        className="mt-2 text-sm text-ink-muted"
        data-testid="insight-export-status"
      >
        {status}
      </p>

      {/* Rendered whether or not the disclosure is open, so `aria-controls`
          always points at something real; `hidden` keeps it out of the
          accessibility tree and out of the reading order until it is wanted. */}
      <div hidden={!open} className="mt-4">
        <DataTableView table={table} id={tableId} />
      </div>
    </section>
  );
}
