/**
 * Planner preferences in Settings: tag choices from the catalogue, quiet
 * hours with midnight-crossing windows, and honest failure states.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import { makePlanFake, planSettings } from "@/test/planFakes";

import { PlanSettingsSection } from "./PlanSettingsSection";

describe("PlanSettingsSection", () => {
  it("offers exactly the catalogue's tags and round-trips a choice", async () => {
    const fake = makePlanFake({
      settings: planSettings({ known_exercise_tags: ["breathing", "grounding", "worry"] }),
    });
    const user = userEvent.setup();
    renderWithProviders(<PlanSettingsSection client={fake.client} />);

    expect(await screen.findByTestId("plan-tag-breathing")).toBeInTheDocument();
    expect(screen.getByTestId("plan-tag-grounding")).toBeInTheDocument();
    expect(screen.getByTestId("plan-tag-worry")).toBeInTheDocument();
    expect(screen.queryByTestId("plan-tag-levitation")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("plan-tag-breathing"));
    await user.click(screen.getByRole("button", { name: "Save plan choices" }));

    await waitFor(() => {
      expect(fake.settings.preferred_exercise_tags).toEqual(["breathing"]);
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved");
  });

  it("keeps Mood Outlook off by default and saves only after explicit opt-in", async () => {
    const fake = makePlanFake();
    const user = userEvent.setup();
    renderWithProviders(<PlanSettingsSection client={fake.client} />);

    const toggle = await screen.findByRole("switch", { name: /show mood outlook on insights/i });
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: "Save plan choices" }));

    await waitFor(() => {
      expect(fake.settings.mood_outlook_enabled).toBe(true);
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved");
  });

  it("turns quiet hours on and saves a midnight-crossing window", async () => {
    const fake = makePlanFake();
    const user = userEvent.setup();
    renderWithProviders(<PlanSettingsSection client={fake.client} />);

    const toggle = await screen.findByTestId("quiet-hours-toggle");
    expect(toggle).not.toBeChecked();
    expect(screen.getByTestId("quiet-start")).toBeDisabled();

    await user.click(toggle);
    expect(screen.getByTestId("quiet-start")).toBeEnabled();

    const start = screen.getByTestId("quiet-start");
    const end = screen.getByTestId("quiet-end");
    // jsdom has no picker UI, so set the values the way a browser commit would.
    await user.clear(start);
    await user.type(start, "22:30");
    await user.clear(end);
    await user.type(end, "06:15");

    await user.click(screen.getByRole("button", { name: "Save plan choices" }));

    await waitFor(() => {
      expect(fake.settings.quiet_hours_enabled).toBe(true);
      expect(fake.settings.quiet_start).toBe("22:30");
      expect(fake.settings.quiet_end).toBe("06:15");
    });
  });

  it("reports a failed save without losing the draft", async () => {
    const fake = makePlanFake();
    fake.failSettings = new ApiError(422, "validation_error", "bad", "req-1");
    const user = userEvent.setup();
    renderWithProviders(<PlanSettingsSection client={fake.client} />);

    await user.click(await screen.findByTestId("plan-tag-worry"));
    await user.click(screen.getByRole("button", { name: "Save plan choices" }));

    expect(await screen.findByRole("status")).toHaveTextContent("could not be saved");
    expect(screen.getByTestId("plan-tag-worry")).toBeChecked();
  });

  it("stays honest when the settings cannot load", async () => {
    const fake = makePlanFake({
      failSettingsLoad: new ApiError(404, "not_found", "gone", "req-1"),
    });
    renderWithProviders(<PlanSettingsSection client={fake.client} />);

    const section = await screen.findByTestId("plan-settings");
    expect(await within(section).findByRole("alert")).toHaveTextContent(
      "Plan settings could not be loaded",
    );
  });
});
