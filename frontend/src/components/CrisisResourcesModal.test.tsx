/**
 * Crisis helpline dialog.
 *
 * The behaviour that matters most is the failure path: a user who needs help
 * must never meet an empty dialog. When the request fails the dialog still
 * states the emergency instruction and offers a retry.
 */

import { axe } from "jest-axe";
import { waitFor } from "@testing-library/react";
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CrisisResourcesModal } from "@/components/CrisisResourcesModal";
import { fetchCrisisResources } from "@/lib/endpoints";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/endpoints", { spy: true });

const RESOURCES = {
  last_verified: "2026-10-01",
  disclaimer: "Placeholder data; verified content arrives with the crisis milestone.",
  resources: [
    {
      id: "us-988",
      region: "US",
      name: "988 Suicide & Crisis Lifeline",
      phone: "988",
      sms: null,
      url: "https://988lifeline.org",
      hours: "24/7",
      description: "Free, confidential support for people in distress.",
      priority: 1,
    },
    {
      id: "uk-samaritans",
      region: "UK & Ireland",
      name: "Samaritans",
      phone: "116 123",
      sms: null,
      url: "https://samaritans.org",
      hours: "24/7",
      description: "A listening ear, any time, for anything.",
      priority: 2,
    },
  ],
};

describe("CrisisResourcesModal", () => {
  it("lists the helplines from the API with tappable numbers", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue(RESOURCES);

    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);

    expect(await screen.findByText("988 Suicide & Crisis Lifeline")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Call 988" })).toHaveAttribute("href", "tel:988");
    expect(screen.getByRole("link", { name: "Call 116 123" })).toHaveAttribute(
      "href",
      "tel:116123",
    );
    const websites = screen.getAllByRole("link", { name: "Visit website" });
    expect(websites[0]).toHaveAttribute("href", "https://988lifeline.org");
    expect(websites[1]).toHaveAttribute("href", "https://samaritans.org");
    expect(screen.getByText(/Last checked by a person on 2026-10-01/)).toBeInTheDocument();
  });

  it("always states the emergency instruction, even before the data arrives", () => {
    vi.mocked(fetchCrisisResources).mockReturnValue(new Promise(() => undefined));

    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);

    expect(screen.getByText(/call your local emergency number now/i)).toBeInTheDocument();
    expect(screen.getByText(/Manovia is not a crisis service/i)).toBeInTheDocument();
    expect(screen.getByText(/Loading helplines/)).toBeInTheDocument();
  });

  it("offers a retry when the request fails", async () => {
    vi.mocked(fetchCrisisResources).mockRejectedValueOnce(new Error("offline"));

    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not be loaded/i);

    vi.mocked(fetchCrisisResources).mockResolvedValue(RESOURCES);
    screen.getByRole("button", { name: "Try again" }).click();

    expect(await screen.findByText("988 Suicide & Crisis Lifeline")).toBeInTheDocument();
    expect(vi.mocked(fetchCrisisResources)).toHaveBeenCalledTimes(2);
  });

  it("has no axe violations", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue(RESOURCES);

    const { container } = renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);

    await screen.findByText("988 Suicide & Crisis Lifeline");
    await waitFor(() => expect(screen.queryByText(/Loading helplines/)).not.toBeInTheDocument());

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
