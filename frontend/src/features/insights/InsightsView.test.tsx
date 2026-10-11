/**
 * The insights page end to end, against a fake transport.
 *
 * Four behaviours get their own test because each one is a promise the product
 * makes on this surface:
 *
 * 1. **Honesty beats decoration.** With three check-ins the page says so and does
 *    not draw a trend — the same rule the backend enforces, asserted here so a
 *    future "just do it client-side" change cannot quietly weaken it.
 * 2. **The limitation is on the page**, not in a design document.
 * 3. **Every chart is readable without seeing it** — a text summary and a table.
 * 4. **Export leaves with the person's data and nothing else.**
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { createQueryClient } from "@/lib/queryClient";
import {
  emptyWeeklySummary,
  insightsPatterns,
  makeInsightsFake,
  sparseInsightsPatterns,
  weeklySummary,
} from "@/test/insightsFakes";

import { InsightsView } from "./InsightsView";

/**
 * Charts are `React.lazy`, so a test that waits for one also waits for Recharts
 * to be transformed and evaluated — seconds on a cold worker, which is why these
 * tests raise the default 5 s limit rather than racing it.
 */
const CHART_TIMEOUT_MS = 20_000;

function renderView(fake: ReturnType<typeof makeInsightsFake>) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <InsightsView client={fake.client} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("InsightsView", () => {
  it("shows the weekly summary, the patterns, and the limitation explainer", async () => {
    const fake = makeInsightsFake();
    renderView(fake);

    // The weekly summary's own words, from the backend template.
    const weeklyText = await screen.findByText(/Your week in review/i);
    const weeklyCard = weeklyText.closest("article");
    expect(weeklyCard).not.toBeNull();
    expect(within(weeklyCard as HTMLElement).getByText(/Average valence/i)).toBeInTheDocument();
    expect(within(weeklyCard as HTMLElement).getByText("3.2")).toBeInTheDocument();
    expect(within(weeklyCard as HTMLElement).getByText("Trending up")).toBeInTheDocument();

    // The charts, with their text alternatives — one named summary per chart, so
    // five distinct regions rather than five identical ones.
    const summaries = await screen.findAllByRole("region", { name: /Text summary: / });
    expect(summaries.map((node) => node.getAttribute("aria-label"))).toEqual([
      "Text summary: 7-day rolling average",
      "Text summary: 30-day rolling average",
      "Text summary: Factor associations",
      "Text summary: Hourly patterns",
      "Text summary: Weekday patterns",
    ]);
    // ...and six data tables (the 7-day chart is rendered twice: page + this list).
    expect(screen.getAllByText("Data table alternative").length).toBeGreaterThanOrEqual(5);

    // And the explainer, which is the honest half of the feature.
    expect(screen.getByText("About these insights")).toBeInTheDocument();
  }, CHART_TIMEOUT_MS);

  it("says 'not enough data' for a person with three check-ins, and draws no trend", async () => {
    const fake = makeInsightsFake({
      patterns: sparseInsightsPatterns(),
      weekly: emptyWeeklySummary(),
    });
    renderView(fake);

    // The weekly card is honest about having nothing to summarise...
    expect(await screen.findByText(/No check-ins this week/i)).toBeInTheDocument();

    // ...the trend is not claimed...
    const summaries = await screen.findAllByRole("region", { name: /Text summary: / });
    const trendSummary = summaries[0];
    expect(trendSummary).toHaveTextContent(/Need at least 7 days with check-ins/i);

    // ...and the factor comparisons say the same.
    const factorSummary = summaries.find((node) =>
      node.textContent?.includes("Not enough data for any factor comparison"),
    );
    expect(factorSummary).toBeDefined();
  }, CHART_TIMEOUT_MS);

  it("shows the empty state rather than an empty dashboard", async () => {
    const fake = makeInsightsFake({
      patterns: insightsPatterns({
        rolling_7: { window_days: 7, points: [], trend: "not_enough_data", trend_note: "" },
        factor_associations: [],
      }),
      weekly: emptyWeeklySummary(),
    });
    renderView(fake);

    expect(await screen.findByText("No insights yet")).toBeInTheDocument();
    expect(
      screen.getByText(/Check in for a few days to see patterns/i),
    ).toBeInTheDocument();
  });

  it("lists every limitation in the 'About these insights' explainer", async () => {
    const fake = makeInsightsFake();
    const user = userEvent.setup();
    renderView(fake);

    await user.click(await screen.findByText("About these insights"));

    expect(screen.getByText(/not predictions or diagnoses/i)).toBeInTheDocument();
    expect(screen.getByText(/do not imply causation/i)).toBeInTheDocument();
    expect(screen.getByText(/at least 7 days with check-ins/i)).toBeInTheDocument();
    expect(screen.getByText(/at least 5 days per group/i)).toBeInTheDocument();
    expect(screen.getByText(/notes are never used/i)).toBeInTheDocument();
    expect(screen.getByText(/no external services are involved/i)).toBeInTheDocument();
  });

  it("never says a factor caused a mood level", async () => {
    const fake = makeInsightsFake();
    renderView(fake);

    await screen.findByText(/Your week in review/i);
    const page = document.body.textContent ?? "";
    for (const phrase of ["causes", "because of", "due to", "leads to", "results in"]) {
      expect(page.toLowerCase()).not.toContain(phrase);
    }
  });

  it("switches the weekly summary language and re-asks the server", async () => {
    const fake = makeInsightsFake({
      weekly: (locale: string) =>
        locale === "hi"
          ? weeklySummary({ locale: "hi", text: "आपके सप्ताह की समीक्षा" })
          : weeklySummary(),
    });
    const user = userEvent.setup();
    renderView(fake);

    await screen.findByText(/Your week in review/i);

    await user.selectOptions(screen.getByLabelText(/Weekly summary language/i), "hi");

    expect(await screen.findByText("आपके सप्ताह की समीक्षा")).toBeInTheDocument();
    expect(fake.locales).toEqual(["en", "hi"]);
  });

  it("exports the person's own data as three CSV files", async () => {
    const fake = makeInsightsFake();
    const user = userEvent.setup();

    // jsdom has neither createObjectURL nor a real download; both are stubbed so
    // the assertions are about *what* was offered, not about the browser's part.
    const created: Blob[] = [];
    const names: string[] = [];
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: (blob: Blob) => {
        created.push(blob);
        return "blob:fake";
      },
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: () => undefined,
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function mockClick(this: HTMLAnchorElement) {
        names.push(this.download);
      });

    renderView(fake);
    await user.click(await screen.findByRole("button", { name: /Export as CSV/i }));

    await waitFor(() => expect(created).toHaveLength(3));
    expect(names).toEqual([
      "mood-entries.csv",
      "factor-associations.csv",
      "time-patterns.csv",
    ]);
    expect(await screen.findByText(/CSV files downloaded/i)).toBeInTheDocument();
    expect(fake.paths.some((path) => path.startsWith("/insights/export"))).toBe(true);
    click.mockRestore();
  });

  it("says so, in words, when the export fails", async () => {
    const fake = makeInsightsFake({ failExport: new Error("network down") });
    const user = userEvent.setup();
    renderView(fake);

    await user.click(await screen.findByRole("button", { name: /Export as CSV/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/Could not prepare the export/i);
  });

  it("keeps a chart's table available to a keyboard user", async () => {
    const fake = makeInsightsFake();
    const user = userEvent.setup();
    renderView(fake);

    const summaries = await screen.findAllByRole("region", { name: /Text summary: / });
    const section = summaries[0]?.closest("section");
    expect(section).not.toBeNull();

    // <details>/<summary> is natively keyboard-operable — no ARIA needed.
    const disclosure = within(section as HTMLElement).getByText("Data table alternative");
    expect(disclosure.tagName).toBe("SUMMARY");

    await user.click(disclosure);
    const table = within(section as HTMLElement).getByRole("table");
    expect(within(table).getAllByRole("row").length).toBeGreaterThan(1);
  }, CHART_TIMEOUT_MS);

  it("has no axe violations with data on screen", async () => {
    const fake = makeInsightsFake();
    const { container } = renderView(fake);

    await screen.findAllByRole("region", { name: /Text summary: / });
    expect(await axe(container)).toHaveNoViolations();
  }, CHART_TIMEOUT_MS);

  it("has no axe violations in the empty state", async () => {
    const fake = makeInsightsFake({
      patterns: insightsPatterns({
        rolling_7: { window_days: 7, points: [], trend: "not_enough_data", trend_note: "" },
        factor_associations: [],
      }),
      weekly: emptyWeeklySummary(),
    });
    const { container } = renderView(fake);

    await screen.findByText("No insights yet");
    expect(await axe(container)).toHaveNoViolations();
  });
});
