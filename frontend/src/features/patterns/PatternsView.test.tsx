import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { PatternsView } from "./PatternsView";
import { loadSourceData } from "./source";
import type { Snapshot } from "./input";
import { synthetic } from "./fixtures";
vi.mock("./source", () => ({ loadSourceData: vi.fn() }));
it("delete prevents an in-flight source read from resurrecting working data", async () => {
  let resolve!: (value: Snapshot) => void;
  vi.mocked(loadSourceData).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  render(<PatternsView />);
  fireEvent.click(screen.getByRole("button", { name: "Read my existing history" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete local graph and experiment" }));
  await act(async () => resolve({ version: 1, days: synthetic(0), themes: [], experiment: null }));
  expect(screen.getByRole("status")).toHaveTextContent(
    "Working graph, themes, journal copy and experiment deleted",
  );
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "A walk, activity" })).not.toBeInTheDocument();
});
