/**
 * Crisis helpline dialog.
 *
 * The behaviour that matters most is the failure path: a user who needs help
 * must never meet an empty dialog. When the request fails the dialog still
 * states the emergency instruction and offers a retry.
 *
 * Migrated in Day 8 from the old `phone`/`sms` payload to the v2 shape
 * (`number`/`type`, computed `tel_href`/`sms_href`, per-entry provenance) and
 * from inline markup to {@link CrisisCard}. Every assertion the Day 5 version
 * made is still made here — tappable numbers, real `tel:` hrefs, website links,
 * the verification date — plus region handling.
 */

import { axe } from "jest-axe";
import { waitFor } from "@testing-library/react";
import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CrisisResourcesModal } from "@/components/CrisisResourcesModal";
import { fetchCrisisResources } from "@/lib/endpoints";
import { crisisResource, TEST_RESPONSE } from "@/test/crisisFixtures";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/endpoints", { spy: true });

describe("CrisisResourcesModal", () => {
  it("lists the helplines from the API with tappable numbers", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue(TEST_RESPONSE);

    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);

    expect(await screen.findByText("Test Crisis Line")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Call 5550100" })).toHaveAttribute(
      "href",
      "tel:5550100",
    );
    expect(screen.getByRole("link", { name: "Call 911" })).toHaveAttribute("href", "tel:911");
    expect(screen.getByRole("link", { name: /Text 55501/ })).toHaveAttribute("href", "sms:55501");
    // Scoped to the entry: the directory entry also has a website link.
    const callLine = screen.getByTestId("crisis-resource-test-call-line");
    expect(within(callLine).getByRole("link", { name: "Visit website" })).toHaveAttribute(
      "href",
      "https://example.test",
    );
    expect(screen.getByText(/Last checked by a person on 2026-10-09/)).toBeInTheDocument();
  });

  it("asks for the whole list when no region is given", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue(TEST_RESPONSE);

    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);
    await screen.findByText("Test Crisis Line");

    expect(fetchCrisisResources).toHaveBeenCalledWith(expect.anything(), undefined);
  });

  it("passes a region through to the request and explains a fallback", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue({
      ...TEST_RESPONSE,
      region: "DEFAULT",
      requested_region: "ZZ",
      fallback_used: true,
    });

    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} region="ZZ" />);

    expect(await screen.findByText(/could not match the region you asked for/)).toBeInTheDocument();
    expect(fetchCrisisResources).toHaveBeenCalledWith(expect.anything(), "ZZ");
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

    vi.mocked(fetchCrisisResources).mockResolvedValue(TEST_RESPONSE);
    screen.getByRole("button", { name: "Try again" }).click();

    expect(await screen.findByText("Test Crisis Line")).toBeInTheDocument();
    expect(vi.mocked(fetchCrisisResources)).toHaveBeenCalledTimes(2);
  });

  it("says when an entry could not be fully confirmed", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue({
      ...TEST_RESPONSE,
      emergency: null,
      resources: [
        crisisResource({
          id: "test-flagged",
          name: "Partly Confirmed Line",
          needs_verification: true,
          verification_note: "Hours conflict between two listings.",
        }),
      ],
      needs_verification: ["test-flagged"],
    });

    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);

    expect(await screen.findByText(/We could not fully confirm this entry/)).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue(TEST_RESPONSE);

    const { container } = renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);

    await screen.findByText("Test Crisis Line");
    await waitFor(() => expect(screen.queryByText(/Loading helplines/)).not.toBeInTheDocument());

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
