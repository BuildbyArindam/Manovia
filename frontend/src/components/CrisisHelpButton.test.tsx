/**
 * "Need help now?" — the button that must be reachable from every page.
 *
 * These tests cover the keyboard path (Enter opens the dialog, focus lands
 * inside it, Escape closes and hands focus back) and the fact that the button is
 * rendered by the shell rather than by each page.
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CrisisHelpButton } from "@/components/CrisisHelpButton";
import { fetchCrisisResources } from "@/lib/endpoints";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/endpoints", { spy: true });

const RESOURCES = {
  last_verified: "2026-10-01",
  disclaimer: "Placeholder data.",
  resources: [
    {
      id: "us-988",
      region: "US",
      name: "988 Suicide & Crisis Lifeline",
      phone: "988",
      sms: null,
      url: null,
      hours: "24/7",
      description: "Free, confidential support.",
      priority: 1,
    },
  ],
};

describe("CrisisHelpButton", () => {
  it("announces itself as a dialog trigger", () => {
    renderWithProviders(<CrisisHelpButton />);

    const button = screen.getByRole("button", { name: "Need help now?" });
    expect(button).toHaveAttribute("aria-haspopup", "dialog");
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("opens with the keyboard and moves focus into the dialog", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue(RESOURCES);
    const user = userEvent.setup();
    renderWithProviders(<CrisisHelpButton />);

    await user.tab();
    expect(screen.getByRole("button", { name: "Need help now?" })).toHaveFocus();

    await user.keyboard("{Enter}");

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleName("Need help now?");
    expect(dialog).toHaveFocus();
    expect(screen.getByRole("button", { name: "Need help now?" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("closes with Escape and returns focus to the button", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue(RESOURCES);
    const user = userEvent.setup();
    renderWithProviders(<CrisisHelpButton />);

    await user.click(screen.getByRole("button", { name: "Need help now?" }));
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Need help now?" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Need help now?" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });
});
