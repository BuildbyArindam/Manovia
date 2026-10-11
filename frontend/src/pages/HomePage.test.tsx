/**
 * The home screen: time-of-day greeting without a name or gender, the plan
 * card, a ten-second check-in, and the always-present help door.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { greetingNow } from "@/features/plan/greeting";
import { fetchCrisisResources } from "@/lib/endpoints";
import { renderWithProviders } from "@/test/utils";
import { TEST_RESPONSE } from "@/test/crisisFixtures";
import { makePlanFake, planToday } from "@/test/planFakes";

import { HomePage } from "./HomePage";

vi.mock("@/lib/endpoints", { spy: true });

function renderHome(fake: ReturnType<typeof makePlanFake>) {
  return renderWithProviders(<HomePage client={fake.client} />);
}

describe("HomePage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("greets by time of day and never invents a name", async () => {
    vi.setSystemTime(new Date("2026-10-11T08:30:00"));
    const fake = makePlanFake();
    renderHome(fake);

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Good morning");
    expect(heading.textContent ?? "").not.toMatch(/friend|there,|buddy/i);

    // The greeting changes with the clock, not with the person.
    vi.setSystemTime(new Date("2026-10-11T20:30:00"));
    expect(greetingNow().headline).toBe("Good evening");
  });

  it("shows the plan, the quick check-in, and the help door", async () => {
    vi.setSystemTime(new Date("2026-10-11T14:00:00"));
    vi.mocked(fetchCrisisResources).mockResolvedValue(TEST_RESPONSE);
    const fake = makePlanFake();
    renderHome(fake);

    expect(await screen.findByTestId("plan-card")).toBeInTheDocument();
    expect(screen.getByTestId("quick-check-in")).toBeInTheDocument();
    expect(screen.getByTestId("home-chat-link")).toHaveAttribute("href", "/chat");

    // "Need help now?" is on the page itself, quiet and permanent.
    const page = screen.getByTestId("home-page");
    expect(within(page).getByRole("button", { name: "Need help now?" })).toBeInTheDocument();
  });

  it("saves a quick check-in with the chosen valence and energy", async () => {
    vi.setSystemTime(new Date("2026-10-11T09:00:00"));
    const fake = makePlanFake();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderHome(fake);

    const checkIn = screen.getByTestId("quick-check-in");
    const saveButton = within(checkIn).getByRole("button", { name: "Save check-in" });
    expect(saveButton).toBeDisabled();

    // Pick the second face (valence 2) and leave energy at its visible default.
    const faces = within(checkIn).getAllByRole("radio");
    expect(faces.length).toBe(5);
    const secondFace = faces[1];
    if (secondFace === undefined) {
      throw new Error("the valence scale must have a second face");
    }
    await user.click(secondFace);
    expect(saveButton).toBeEnabled();

    await user.click(saveButton);

    await waitFor(() => {
      expect(within(checkIn).getByRole("status")).toHaveTextContent("Saved");
    });
    expect(fake.moodSent).toEqual([{ valence: 2, energy: 3 }]);
  });

  it("offers a full check-in door as well", async () => {
    vi.setSystemTime(new Date("2026-10-11T09:00:00"));
    const fake = makePlanFake();
    renderHome(fake);

    expect(screen.getByRole("link", { name: "Full mood check-in" })).toHaveAttribute(
      "href",
      "/mood",
    );
  });

  it("shows quiet-hours plans without inventing suggestions", async () => {
    vi.setSystemTime(new Date("2026-10-11T23:00:00"));
    const fake = makePlanFake({
      plan: planToday({ items: [], count: 0, quiet_hours_active: true }),
    });
    renderHome(fake);

    expect(await screen.findByTestId("plan-quiet-hours")).toBeInTheDocument();
  });
});
