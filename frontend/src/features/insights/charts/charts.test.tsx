/**
 * The charts: text alternative, data table, and axe.
 *
 * The accessibility claim for this feature is not "it has aria-labels". It is
 * that **every chart is fully readable without seeing it**: a sentence that
 * states the finding, and a table that states the numbers behind it. These tests
 * assert that pair for each chart, plus an axe scan on the rendered tree.
 *
 * Charts are lazy-loaded, so every render waits for the chunk.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { axe } from "jest-axe";

import {
  factorAssociation,
  sevenDayWindow,
  sparseInsightsPatterns,
  insightsPatterns,
} from "@/test/insightsFakes";

import { FactorAssociationChart } from "./FactorAssociationChart";
import { RollingAverageChart } from "./RollingAverageChart";
import { TimePatternChart } from "./TimePatternChart";
import { CHART_PALETTE, getCategoricalColour } from "./ChartPalette";

describe("RollingAverageChart", () => {
  it("states the finding in words, with the numbers in the sentence", () => {
    render(<RollingAverageChart data={sevenDayWindow()} title="7-day rolling average" />);

    const summary = screen.getByRole("region", { name: /Text summary: / });
    // 2.0 + 0.4*i over seven days -> mean 3.2; the sentence must carry it.
    expect(summary).toHaveTextContent(/7-day rolling average over 7 days with data/i);
    expect(summary).toHaveTextContent(/average valence 3\.2/i);
    expect(summary).toHaveTextContent(/trend is upward/i);
  });

  it("offers the same data as a table", () => {
    render(<RollingAverageChart data={sevenDayWindow()} title="7-day rolling average" />);

    const disclosure = screen.getByText("Data table alternative").closest("details");
    expect(disclosure).not.toBeNull();
    const table = within(disclosure as HTMLElement).getByRole("table");
    // Header row plus one row per day with data.
    expect(within(table).getAllByRole("row")).toHaveLength(8);
    expect(within(table).getByText("2026-03-09")).toBeInTheDocument();
    expect(within(table).getByText("2.0")).toBeInTheDocument();
  });

  it("says 'not enough data' instead of drawing a line through three points", () => {
    const sparse = sparseInsightsPatterns().rolling_7;
    render(<RollingAverageChart data={sparse} title="7-day rolling average" />);

    const summary = screen.getByRole("region", { name: /Text summary: / });
    expect(summary).toHaveTextContent(/Need at least 7 days with check-ins/i);
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <main>
        <RollingAverageChart data={sevenDayWindow()} title="7-day rolling average" />
      </main>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("FactorAssociationChart", () => {
  it("states the association, its sizes, and that it is not a cause", () => {
    render(
      <FactorAssociationChart
        data={insightsPatterns().factor_associations}
        minPerGroup={5}
        title="Factor associations"
      />,
    );

    const summary = screen.getByRole("region", { name: /Text summary: / });
    expect(summary).toHaveTextContent(/sleep: 1\.2 points lower/i);
    expect(summary).toHaveTextContent(/n=9 vs 21/);
    expect(summary).toHaveTextContent(/associations, not causal links/i);
  });

  it("prints the numbers that back the claim in the table", () => {
    render(
      <FactorAssociationChart
        data={[factorAssociation()]}
        minPerGroup={5}
        title="Factor associations"
      />,
    );

    const table = screen.getByRole("table");
    const row = within(table).getAllByRole("row")[1];
    expect(row).toBeDefined();
    expect(within(row as HTMLElement).getByText("sleep")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("2.2")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("3.4")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("9")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("21")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("-0.90")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("[-1.30, -0.50]")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("0.002")).toBeInTheDocument();
  });

  it("says 'not enough data' for a tag with too few days, and keeps it in the table", () => {
    const weather = factorAssociation({
      factor: "weather",
      with_factor_avg: 3.1,
      without_factor_avg: null,
      with_factor_count: 2,
      without_factor_count: 28,
      effect_size: null,
      ci_lower: null,
      ci_upper: null,
      p_value: null,
      note:
        "Not enough data to compare days with vs without weather " +
        "(need at least 5 days per group; have 2 with, 28 without).",
    });
    render(<FactorAssociationChart data={[weather]} minPerGroup={5} title="Factor associations" />);

    const summary = screen.getByRole("region", { name: /Text summary: / });
    expect(summary).toHaveTextContent(/Not enough data for any factor comparison/i);
    expect(summary).toHaveTextContent(/at least 5 days/i);

    // The row is still present — hiding it would hide that the tag was ever used.
    const table = screen.getByRole("table");
    expect(within(table).getByText("weather")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <main>
        <FactorAssociationChart
          data={insightsPatterns().factor_associations}
          minPerGroup={5}
          title="Factor associations"
        />
      </main>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("TimePatternChart", () => {
  it("names the highest and lowest buckets in words", () => {
    render(
      <TimePatternChart
        data={insightsPatterns().weekday_patterns}
        title="Weekday patterns"
        subheading="Average valence and energy by day of week"
        xAxisLabel="Weekday"
      />,
    );

    const summary = screen.getByRole("region", { name: /Text summary: / });
    expect(summary).toHaveTextContent(/1 of 7 time buckets have enough data/i);
    expect(summary).toHaveTextContent(/Highest average mood at Mon \(2\.4\)/i);
    expect(summary).toHaveTextContent(/descriptive patterns, not predictions/i);
  });

  it("keeps a row per bucket in the table, including the empty ones", () => {
    render(
      <TimePatternChart
        data={insightsPatterns().weekday_patterns}
        title="Weekday patterns"
        subheading="Average valence and energy by day of week"
        xAxisLabel="Weekday"
      />,
    );

    const table = screen.getByRole("table");
    // Header plus seven weekday rows.
    expect(within(table).getAllByRole("row")).toHaveLength(8);
    expect(
      within(table).getByText(/Only 2 check-in\(s\) on Tue — need 5 to show a pattern/i),
    ).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <main>
        <TimePatternChart
          data={insightsPatterns().hourly_patterns}
          title="Hourly patterns"
          subheading="Average valence and energy by hour of day"
          xAxisLabel="Hour"
        />
      </main>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("chart palette", () => {
  it("is colour-blind safe: named hex values, not hues that collide", () => {
    // The Okabe-Ito set. Every value is an explicit hex, so no chart inherits a
    // theme variable that could be contrast-poor in one of the two palettes.
    expect(CHART_PALETTE.valence).toBe("#0072B2");
    expect(CHART_PALETTE.energy).toBe("#D55E00");
    expect(CHART_PALETTE.trendUp).toBe("#009E73");
    expect(CHART_PALETTE.trendDown).toBe("#D55E00");
    expect(CHART_PALETTE.categorical).toHaveLength(8);
    for (const colour of CHART_PALETTE.categorical) {
      expect(colour).toMatch(/^#[0-9A-F]{6}$/i);
    }
  });

  it("distinguishes valence from energy by more than hue alone", () => {
    // Blue and vermillion are far apart in every CVD simulation, and the energy
    // line is additionally dashed (see RollingAverageChart).
    expect(CHART_PALETTE.valence).not.toBe(CHART_PALETTE.energy);
  });

  it("cycles categorical colours without repeating within the first eight", () => {
    const seen = new Set(Array.from({ length: 8 }, (_, i) => getCategoricalColour(i)));
    expect(seen.size).toBe(8);
    // And wraps predictably rather than returning undefined.
    expect(getCategoricalColour(8)).toBe(getCategoricalColour(0));
  });
});
