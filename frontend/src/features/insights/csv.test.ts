/**
 * The CSV export.
 *
 * The escaping rules get their own tests because a CSV that quotes wrongly does
 * not merely look odd — it moves data between columns, and in a file about
 * somebody's own numbers that is a small act of vandalism. The localised labels
 * in these tables make the comma and quote cases likely rather than theoretical:
 * a sentence column contains both.
 *
 * The downloader is tested through the guard, not around it: one case where the
 * browser cannot do it (and must say so), one where it can (and must clean up
 * after itself).
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { buildCsv, csvFilename, csvFromTable, downloadCsv } from "./csv";
import type { DataTable } from "./series";

const TABLE: DataTable = {
  caption: "Mood by tag",
  columns: [
    { key: "tag", label: "Tag" },
    { key: "difference", label: "Difference (points)", numeric: true },
    { key: "verdict", label: "What we can say" },
  ],
  rows: [
    {
      tag: "sleep",
      difference: -0.85,
      verdict: 'On days you tagged sleep, your mood was about 0.85 points lower, "on average".',
    },
    { tag: "work", difference: null, verdict: null },
  ],
  note: "An empty cell means the comparison did not clear the rules.",
};

describe("buildCsv", () => {
  it("writes a header row and one row per line, CRLF terminated", () => {
    const csv = buildCsv(
      ["a", "b"],
      [
        ["1", "2"],
        ["3", "4"],
      ],
    );
    expect(csv).toBe("a,b\r\n1,2\r\n3,4\r\n");
  });

  it("quotes a field containing a comma, a quote or a newline", () => {
    expect(buildCsv(["x"], [["plain"]])).toBe("x\r\nplain\r\n");
    expect(buildCsv(["x"], [["a,b"]])).toBe('x\r\n"a,b"\r\n');
    expect(buildCsv(["x"], [["line\nbreak"]])).toBe('x\r\n"line\nbreak"\r\n');
  });

  it("doubles a quote inside a quoted field", () => {
    expect(buildCsv(["x"], [['say "no"']])).toBe('x\r\n"say ""no"""\r\n');
  });

  it("leaves a null cell empty rather than writing the word null", () => {
    expect(buildCsv(["a", "b"], [["1", null]])).toBe("a,b\r\n1,\r\n");
  });

  it("stringifies numbers without quotes", () => {
    expect(buildCsv(["n"], [[-0.85]])).toBe("n\r\n-0.85\r\n");
  });

  it("writes a lone header when there are no rows", () => {
    expect(buildCsv(["a", "b"], [])).toBe("a,b\r\n");
  });
});

describe("csvFromTable", () => {
  it("uses the column labels as headers and keeps the column order", () => {
    const [header, first] = csvFromTable(TABLE).split("\r\n");
    expect(header).toBe("Tag,Difference (points),What we can say");
    expect(first).toContain("sleep,-0.85,");
  });

  it("quotes the sentence column, which contains both a comma and quotes", () => {
    const csv = csvFromTable(TABLE);
    expect(csv).toContain(
      '"On days you tagged sleep, your mood was about 0.85 points lower, ""on average""."',
    );
  });

  it("keeps an empty cell empty for a comparison that was held back", () => {
    const csv = csvFromTable(TABLE);
    expect(csv).toContain("\r\nwork,,\r\n");
  });

  it("carries the reading note into the export as a comment line", () => {
    const csv = csvFromTable(TABLE);
    expect(csv.endsWith("# An empty cell means the comparison did not clear the rules.\r\n")).toBe(
      true,
    );
  });

  it("omits the comment when a table has no note", () => {
    const csv = csvFromTable({ ...TABLE, note: undefined });
    expect(csv).not.toContain("#");
  });
});

describe("csvFilename", () => {
  it("slugs the topic and stamps the day the data came from", () => {
    expect(csvFilename("Mood and Tags", "2026-10-11")).toBe(
      "manovia-insights-mood-and-tags-2026-10-11.csv",
    );
  });

  it("survives a topic with nothing to slug", () => {
    expect(csvFilename("!!!", "2026-10-11")).toBe("manovia-insights--2026-10-11.csv");
  });
});

describe("downloadCsv", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("reports failure rather than throwing when the browser has no object URLs", () => {
    Object.defineProperty(URL, "createObjectURL", {
      value: undefined,
      configurable: true,
      writable: true,
    });

    expect(downloadCsv("out.csv", "a,b\r\n")).toBe(false);
  });

  it("builds a blob, clicks a named link, and revokes the URL afterwards", () => {
    const chunks: string[] = [];
    class RecordingBlob {
      constructor(parts: string[]) {
        chunks.push(...parts);
      }
    }
    vi.stubGlobal("Blob", RecordingBlob);

    const createObjectURL = vi.fn(() => "blob:manovia");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      value: createObjectURL,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: revokeObjectURL,
      configurable: true,
      writable: true,
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    vi.useFakeTimers();

    expect(downloadCsv("out.csv", "a,b\r\n1,2\r\n")).toBe(true);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    // The byte-order mark, so Excel reads the localised labels as UTF-8.
    expect(chunks.join("").startsWith("\ufeff")).toBe(true);
    expect(chunks.join("")).toContain("a,b\r\n1,2\r\n");

    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:manovia");
  });

  it("names the download from the filename it was given", () => {
    vi.stubGlobal("Blob", class {});
    Object.defineProperty(URL, "createObjectURL", {
      value: vi.fn(() => "blob:x"),
      configurable: true,
      writable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: vi.fn(),
      configurable: true,
      writable: true,
    });
    const seen: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(
      this: HTMLAnchorElement,
    ) {
      seen.push(this.download);
    });

    downloadCsv("manovia-insights-mood-2026-10-11.csv", "a\r\n");
    expect(seen).toEqual(["manovia-insights-mood-2026-10-11.csv"]);
  });
});
