/**
 * The insights page end to end, against a fake transport.
 *
 * The promises being tested here are the ones the feature is actually about, so
 * they are stated as tests rather than left to a manual pass:
 *
 * - every chart on the page has a text summary that is rendered *whether or not*
 *   anything is opened, and a data table with the same numbers;
 * - the charts are hidden from assistive technology, because a picture of a
 *   number cannot be read and the number itself is already on the page;
 * - the disclosure and the export button work from the keyboard;
 * - a page with too little data shows the "not enough data" answer rather than
 *   an empty axis pretending to be a result;
 * - nothing the page renders — including its own chrome copy — uses causal
 *   language about somebody's mood;
 * - and axe finds no violations across the whole thing.
 */

import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, afterEach, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { makeInsightsFake, patternInsights, weeklyInsights } from "@/test/insightsFakes";
import { renderWithProviders } from "@/test/utils";

import { InsightsView } from "./InsightsView";
import { INSIGHTS_COPY } from "./copy";

/** The scan the backend runs over its templates, repeated over the whole page. */
const CAUSAL_BLOCKLIST = [
  "because",
  "causes",
  "caused",
  "due to",
  "leads to",
  "results in",
  "as a result",
  "the reason",
  "why your",
];

function render(fake = makeInsightsFake()) {
  const result = renderWithProviders(<InsightsView client={fake.client} />);
  return { ...result, fake };
}

/** Every panel that draws a picture. */
async function panelsWithCharts(): Promise<HTMLElement[]> {
  await screen.findByTestId("insights-about");
  return [...document.querySelectorAll<HTMLElement>("[data-insight-panel]")].filter(
    (panel) => panel.querySelector("svg") !== null,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the text alternative", () => {
  it("renders the server's sentences before any chart appears", async () => {
    render();

    const summary = await screen.findByTestId("insights-patterns-summary");
    expect(summary).toHaveTextContent("about 0.85 points lower");

    const charts = await panelsWithCharts();
    expect(charts.length).toBeGreaterThan(0);
    for (const chart of charts) {
      // POSITION_PRECEDING: the summary block comes first in the document, so a
      // reader meets the words before the picture, not after it.
      expect(
        summary.compareDocumentPosition(chart) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it("gives every chart a text summary that is present without opening anything", async () => {
    render();

    const charts = await panelsWithCharts();
    for (const panel of charts) {
      const summary = within(panel).getByTestId("insight-summary");
      expect(summary.textContent?.trim().length).toBeGreaterThan(20);
      // Visible, not sr-only, not inside a closed disclosure.
      expect(summary).toBeVisible();
    }
  });

  it("hides the charts from assistive technology and names the scroll region", async () => {
    render();

    const charts = await panelsWithCharts();
    for (const panel of charts) {
      const svg = panel.querySelector("svg");
      expect(svg).not.toBeNull();
      expect(svg?.closest("[aria-hidden='true']")).not.toBeNull();

      const region = within(panel).getByRole("group");
      const heading = within(panel).getByRole("heading").textContent ?? "";
      expect(region).toHaveAccessibleName(INSIGHTS_COPY.chartRegion(heading));
    }
  });

  it("puts the same numbers in a data table behind every chart", async () => {
    const user = userEvent.setup();
    render();

    const charts = await panelsWithCharts();
    expect(charts.length).toBeGreaterThanOrEqual(4);

    for (const panel of charts) {
      const toggle = within(panel).getByRole("button", {
        name: INSIGHTS_COPY.showTable,
      });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      await user.click(toggle);

      const table = within(panel).getByRole("table");
      expect(table).toHaveAccessibleName(expect.any(String) as string);
      expect(within(table).getAllByRole("columnheader").length).toBeGreaterThan(1);
      expect(within(panel).getByRole("button", { name: INSIGHTS_COPY.hideTable })).toHaveAttribute(
        "aria-expanded",
        "true",
      );
    }
  });

  it("opens the table from the keyboard alone", async () => {
    const user = userEvent.setup();
    render();

    const [first] = await panelsWithCharts();
    const panel = first as HTMLElement;
    const toggle = within(panel).getByRole("button", { name: INSIGHTS_COPY.showTable });

    // Tab to it rather than clicking it: the point is that no pointer is needed.
    await user.tab();
    while (document.activeElement !== toggle) {
      await user.tab();
    }
    expect(toggle).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(within(panel).getByRole("table")).toBeVisible();
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    await user.keyboard("{Enter}");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("shows held-back comparisons in the table but not in the chart", async () => {
    render();

    const panel = await screen.findByTestId("insight-tags-valence");
    const chart = within(panel).getByTestId("difference-chart");

    // Reported: sleep and friends. Held back: work (three check-ins) and study
    // (a difference smaller than the floor).
    expect(chart).toHaveTextContent("sleep");
    expect(chart).toHaveTextContent("friends");
    expect(chart).not.toHaveTextContent("work");
    expect(chart).not.toHaveTextContent("study");

    fireEvent.click(within(panel).getByRole("button", { name: INSIGHTS_COPY.showTable }));
    const table = within(panel).getByRole("table");
    expect(table).toHaveTextContent("work");
    expect(table).toHaveTextContent("study");
    expect(table).toHaveTextContent("-0.85");
    expect(table).toHaveTextContent("0.0031");
    // The reason a comparison was held back travels with the table, as its
    // accessible description rather than as loose text beside it.
    expect(table).toHaveAccessibleDescription(/did not clear the rules/);
  });
});

describe("provenance and the explainer", () => {
  it("says how many check-ins the numbers came from, and in which timezone", async () => {
    render();

    const provenance = await screen.findByTestId("insights-provenance");
    expect(provenance).toHaveTextContent("58 of your 60 check-ins");
    expect(provenance).toHaveTextContent("Asia/Kolkata");
  });

  it("renders the explainer with its limitations and its methods", async () => {
    render();

    const about = await screen.findByTestId("insights-about");
    expect(about).toHaveTextContent("About these insights");
    expect(screen.getByTestId("insights-not-causal")).toHaveTextContent("not a cause");
    expect(about).toHaveTextContent("What this cannot tell you");
    expect(about).toHaveTextContent("shuffling the same check-ins");
    expect(about).toHaveTextContent("not therapy");
  });
});

describe("when there is not enough data", () => {
  it("draws no chart and says so in the server's own words", async () => {
    const sparse = patternInsights({
      enough_data: false,
      considered_check_ins: 3,
      total_check_ins: 3,
      summary: ["Not enough data yet to describe a pattern. Three check-ins is not enough."],
      rolling: [],
      factors: {
        comparisons: 16,
        family_alpha: 0.003125,
        min_group_n: 5,
        min_effect_points: 0.4,
        associations: [],
      },
      time_of_day: {
        ...patternInsights().time_of_day,
        buckets: [],
        sentence: "There are not enough check-ins in any part of the day to compare them.",
      },
      weekday: {
        ...patternInsights().weekday,
        buckets: [],
        sentence: "There are not enough check-ins on any day of the week to compare them.",
      },
    });
    render(makeInsightsFake({ patterns: sparse }));

    expect(await screen.findByTestId("insights-not-enough")).toHaveTextContent(
      "Not enough data yet",
    );
    expect(screen.getByTestId("insights-patterns-summary")).toHaveTextContent(
      "Not enough data yet to describe a pattern",
    );
    expect(screen.queryByTestId("rolling-chart")).not.toBeInTheDocument();
    expect(screen.queryByTestId("difference-chart")).not.toBeInTheDocument();
    // The panels are still there, still saying what they can.
    expect(screen.getByTestId("insight-time-of-day")).toHaveTextContent(
      "not enough check-ins in any part of the day",
    );
  });

  it("still offers the tables, because a table of your own numbers is not a claim", async () => {
    const empty = patternInsights({ rolling: [], enough_data: false });
    render(makeInsightsFake({ patterns: empty }));

    const panel = await screen.findByTestId("insight-time-of-day");
    fireEvent.click(within(panel).getByRole("button", { name: INSIGHTS_COPY.showTable }));
    expect(within(panel).getByRole("table")).toBeVisible();
  });
});

describe("export", () => {
  it("says so when the browser will not start a download", async () => {
    // jsdom does implement object URLs, so the guarded path has to be forced:
    // this is what a browser without `URL.createObjectURL` looks like to the
    // button, and the answer has to be a sentence rather than an exception.
    Object.defineProperty(URL, "createObjectURL", {
      value: undefined,
      configurable: true,
      writable: true,
    });

    const user = userEvent.setup();
    render();

    const panel = await screen.findByTestId("insight-rolling-valence");
    await user.click(within(panel).getByRole("button", { name: INSIGHTS_COPY.downloadCsv }));

    expect(within(panel).getByTestId("insight-export-status")).toHaveTextContent(
      "would not start a download",
    );
  });

  it("builds a CSV with the table's headers, its rows and its reading note", async () => {
    // jsdom has no object URLs; the export is guarded for exactly that case, so
    // the test supplies one and inspects the blob the button handed it.
    // jsdom's Blob has no `.text()`, so the constructor is stubbed with one that
    // keeps the parts it was given: the assertion is on the CSV string the button
    // handed over, which is the thing under test.
    const parts: string[] = [];
    class RecordingBlob {
      constructor(chunks: string[]) {
        parts.push(...chunks);
      }
    }
    vi.stubGlobal("Blob", RecordingBlob);
    // An <a href="blob:..."> click makes jsdom log "Not implemented: navigation".
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    const blobs: string[] = [];
    const createObjectURL = vi.fn((blob: Blob) => {
      blobs.push(String(blob));
      return "blob:manovia";
    });
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

    const user = userEvent.setup();
    render();
    const panel = await screen.findByTestId("insight-tags-valence");
    await user.click(within(panel).getByRole("button", { name: INSIGHTS_COPY.downloadCsv }));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(blobs).toHaveLength(1);
    const text = parts.join("");
    expect(text).toContain("Tag,Check-ins with the tag");
    expect(text).toContain("sleep,24,34");
    expect(text).toContain("-0.85");
    // The note that explains what an empty cell means travels with the export.
    expect(text).toContain("# An empty last column");
  });
});

describe("failure states", () => {
  it("offers a way back when the request fails", async () => {
    render(
      makeInsightsFake({
        // A 4xx: the shared query client does not retry those, so the panel
        // appears immediately instead of after a backoff nobody is testing.
        failPatterns: new ApiError(422, "invalid_timezone", "Unknown timezone.", "req-1"),
      }),
    );

    const panel = await screen.findByTestId("insights-error");
    expect(panel).toHaveTextContent(INSIGHTS_COPY.errorTitle);
    expect(within(panel).getByRole("button", { name: INSIGHTS_COPY.retry })).toBeVisible();
  });

  it("points at the consents when that is why the page is empty", async () => {
    render(
      makeInsightsFake({
        failPatterns: new ApiError(403, "consent_required", "Consent needed.", "req-2"),
        failWeekly: new ApiError(403, "consent_required", "Consent needed.", "req-3"),
      }),
    );

    const panels = await screen.findAllByTestId("insights-error");
    expect(panels[0]).toHaveTextContent(INSIGHTS_COPY.consentTitle);
    expect(within(panels[0] as HTMLElement).getByRole("link")).toHaveAttribute("href", "/settings");
    expect(within(panels[0] as HTMLElement).queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("the weekly surface", () => {
  it("keeps an empty week in the table rather than dropping it", async () => {
    render(makeInsightsFake({ weekly: weeklyInsights() }));

    const panel = await screen.findByTestId("insight-weekly");
    expect(panel).toHaveTextContent("Here are your last 4 weeks");
    fireEvent.click(within(panel).getByRole("button", { name: INSIGHTS_COPY.showTable }));

    const table = within(panel).getByRole("table");
    expect(table).toHaveTextContent("28 September 2026");
    const rows = within(table).getAllByRole("row");
    // One header row and four weeks, including the one with nothing in it.
    expect(rows).toHaveLength(5);
  });
});

describe("wording and accessibility", () => {
  it("uses no causal language anywhere on the page, chrome included", async () => {
    render();
    await screen.findByTestId("insights-about");

    // Open every disclosure so the scan covers the tables and their notes too.
    for (const toggle of screen.queryAllByRole("button", { name: INSIGHTS_COPY.showTable })) {
      fireEvent.click(toggle);
    }

    const text = document.body.textContent?.toLowerCase() ?? "";
    for (const token of CAUSAL_BLOCKLIST) {
      expect(text, `causal wording on the page: ${token}`).not.toContain(token);
    }
  });

  it("renders the page without accessibility violations", async () => {
    const { container } = render();
    await screen.findByTestId("insights-about");

    for (const toggle of screen.queryAllByRole("button", { name: INSIGHTS_COPY.showTable })) {
      fireEvent.click(toggle);
    }

    expect(await axe(container)).toHaveNoViolations();
  });
});
