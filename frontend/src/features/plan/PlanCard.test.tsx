/**
 * The plan card: shows each suggestion with its "why", settles items with
 * Done/Skip, honours quiet hours, and keeps the support door visible.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { fetchCrisisResources } from "@/lib/endpoints";
import { renderWithProviders } from "@/test/utils";
import { TEST_RESPONSE } from "@/test/crisisFixtures";
import { makePlanFake, planItem, planToday } from "@/test/planFakes";

import { PlanCard } from "./PlanCard";

vi.mock("@/lib/endpoints", { spy: true });

function renderPlanCard(fake: ReturnType<typeof makePlanFake>) {
  return renderWithProviders(<PlanCard client={fake.client} />);
}

describe("PlanCard", () => {
  it("shows each suggestion with a why and both actions", async () => {
    const fake = makePlanFake({
      plan: planToday({
        items: [
          planItem({ id: "a", kind: "check_in", title: "Take a first check-in" }),
          planItem({
            id: "b",
            kind: "exercise",
            ref: "box-breathing",
            title: "A short exercise for the morning",
            why: "You have done exercises on 3 of the last 7 days.",
          }),
        ],
        count: 2,
      }),
    });
    const { container } = renderPlanCard(fake);

    const first = await screen.findByTestId("plan-item-check_in");
    expect(first).toHaveTextContent("Take a first check-in");
    expect(first).toHaveTextContent("Why this?");
    expect(within(first).getByRole("button", { name: "Done" })).toBeInTheDocument();
    expect(within(first).getByRole("button", { name: "Skip" })).toBeInTheDocument();

    const exercise = screen.getByTestId("plan-item-exercise");
    expect(within(exercise).getByRole("link", { name: "Open exercise" })).toHaveAttribute(
      "href",
      "/exercises/box-breathing",
    );

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it("never renders more than three items", async () => {
    const fake = makePlanFake({
      plan: planToday({
        items: [
          planItem({ id: "a", kind: "check_in" }),
          planItem({ id: "b", kind: "exercise", ref: "body-scan" }),
          planItem({ id: "c", kind: "rest", title: "Today can be a rest day" }),
        ],
        count: 3,
      }),
    });
    renderPlanCard(fake);

    await screen.findByTestId("plan-item-check_in");
    const items = screen.getAllByTestId(/^plan-item-/);
    expect(items.length).toBeLessThanOrEqual(3);
  });

  it("marks an item done and reflects the settled status", async () => {
    const fake = makePlanFake();
    const user = userEvent.setup();
    renderPlanCard(fake);

    await user.click(await screen.findByRole("button", { name: "Done" }));

    expect(fake.done).toEqual(["item-1"]);
    await waitFor(() => {
      expect(screen.getByTestId("plan-item-check_in")).toHaveAttribute("data-status", "done");
    });
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
    });
  });

  it("skips an item without asking for a reason", async () => {
    const fake = makePlanFake();
    const user = userEvent.setup();
    renderPlanCard(fake);

    await user.click(await screen.findByRole("button", { name: "Skip" }));

    expect(fake.skipped).toEqual(["item-1"]);
    await waitFor(() => {
      expect(screen.getByTestId("plan-item-check_in")).toHaveAttribute("data-status", "skipped");
    });
  });

  it("explains quiet hours instead of suggesting anything", async () => {
    const fake = makePlanFake({
      plan: planToday({ items: [], count: 0, quiet_hours_active: true }),
    });
    renderPlanCard(fake);

    expect(await screen.findByTestId("plan-quiet-hours")).toHaveTextContent("Quiet hours");
    expect(screen.queryAllByTestId(/^plan-item-/)).toHaveLength(0);
  });

  it("treats an empty plan as a fine answer", async () => {
    const fake = makePlanFake({ plan: planToday({ items: [], count: 0 }) });
    renderPlanCard(fake);

    expect(await screen.findByTestId("plan-empty")).toHaveTextContent("Nothing to suggest");
  });

  it("shows support options for a support item and opens the helplines", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue(TEST_RESPONSE);
    const fake = makePlanFake({
      plan: planToday({
        support_only: true,
        items: [
          planItem({
            id: "s",
            kind: "support",
            title: "Support options are here",
            why: "Your most recent screening included an answer we show support options for.",
          }),
        ],
        count: 1,
      }),
    });
    const user = userEvent.setup();
    renderPlanCard(fake);

    const card = await screen.findByTestId("plan-item-support");
    expect(within(card).queryByRole("button", { name: "Done" })).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "See support options" }));
    expect(await screen.findByRole("dialog")).toHaveAccessibleName("Need help now?");
  });

  it("surfaces a settle failure without losing the plan", async () => {
    const fake = makePlanFake();
    fake.failSettle = new ApiError(500, "server_error", "boom", "req-1");
    const user = userEvent.setup();
    renderPlanCard(fake);

    await user.click(await screen.findByRole("button", { name: "Done" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("could not be saved");
    expect(screen.getByTestId("plan-item-check_in")).toHaveAttribute("data-status", "pending");
  });

  it("stays gentle when the plan cannot load", async () => {
    // A 4xx ApiError skips the query retry, so the error state is immediate.
    const fake = makePlanFake({
      failToday: new ApiError(404, "not_found", "gone", "req-1"),
    });
    renderPlanCard(fake);

    expect(await screen.findByText(/The plan could not be loaded right now/)).toBeInTheDocument();
  });
});
