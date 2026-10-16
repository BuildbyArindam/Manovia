/**
 * The no-hardware path (Day 42): import a CSV export from a health app.
 *
 * The file is read and processed in this browser (`import/csvImport.ts` +
 * the shared signal pipeline). Nothing is uploaded — there is no network call
 * anywhere on this path, and the "local only" claim is pinned by tests.
 *
 * WELLNESS FEATURE. Not a medical device; this is a trend view of your own
 * data, not a medical record import.
 */

import { useRef, useState, type ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";

import { CsvImportError, parseHrCsv } from "../import/csvImport";
import type { RRSample } from "../signal/types";

export interface CsvImportPanelProps {
  onImported: (samples: RRSample[]) => void;
}

/** `File.text()` with a FileReader fallback (older browsers, jsdom). */
async function readFileText(file: File): Promise<string> {
  if (typeof file.text === "function") {
    return file.text();
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("could not read the file"));
    reader.readAsText(file);
  });
}

export function CsvImportPanel({ onImported }: CsvImportPanelProps): ReactElement {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [result, setResult] = useState<{ count: number; skipped: number; warnings: string[] } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  const handleFile = async (file: File): Promise<void> => {
    setError(null);
    setResult(null);
    try {
      const text = await readFileText(file);
      const parsed = parseHrCsv(text);
      setResult({
        count: parsed.samples.length,
        skipped: parsed.skippedRows,
        warnings: parsed.warnings,
      });
      onImported(parsed.samples);
    } catch (caught) {
      const message = caught instanceof CsvImportError ? caught.message : String(caught);
      setError(message);
    }
  };

  return (
    <section
      className="space-y-4 rounded-2xl border border-border bg-surface p-6"
      aria-labelledby="biofeedback-csv-heading"
      data-testid="csv-panel"
    >
      <h2 id="biofeedback-csv-heading" className="text-xl">
        {t("biofeedback.csv.heading")}
      </h2>
      <p className="text-ink-muted">{t("biofeedback.csv.body")}</p>
      <p className="rounded-xl border border-border bg-surface-muted p-3 text-sm">
        {t("biofeedback.csv.localOnly")}
      </p>
      <div>
        <label className="font-semibold" htmlFor="biofeedback-csv-input">
          {t("biofeedback.csv.choose")}
        </label>
        <input
          id="biofeedback-csv-input"
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          // File inputs have a large intrinsic width: without an explicit
          // width cap they push a 320–360 px screen into horizontal scroll
          // (caught by the Day 25 zoom sweep).
          className="mt-2 block w-full max-w-md"
          data-testid="csv-input"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file !== undefined) {
              void handleFile(file);
            }
          }}
        />
      </div>
      <p className="text-sm text-ink-muted">{t("biofeedback.csv.formats")}</p>
      {result !== null ? (
        <div role="status" data-testid="csv-result">
          <p>{t("biofeedback.csv.imported", { count: result.count, skipped: result.skipped })}</p>
          {result.warnings.map((warning) => (
            <p key={warning} className="text-sm text-ink-muted">
              {warning}
            </p>
          ))}
        </div>
      ) : null}
      {error !== null ? (
        <p role="alert" data-testid="csv-error">
          {t("biofeedback.csv.error", { message: error })}
        </p>
      ) : null}
    </section>
  );
}
