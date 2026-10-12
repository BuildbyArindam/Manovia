/**
 * Day 25: "Need help now?" with no connection.
 *
 * The query must still run while `navigator.onLine` is false — the service
 * worker answers it from the cache — instead of TanStack Query pausing it and
 * leaving "Loading helplines…" on screen forever. The dialog then says it is
 * showing the saved copy; with nothing saved yet it says that, next to the
 * emergency instruction that is always there.
 */

import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CrisisResourcesModal } from "@/components/CrisisResourcesModal";
import { fetchCrisisResources } from "@/lib/endpoints";
import { TEST_RESPONSE } from "@/test/crisisFixtures";
import { resetOnline, setOnline } from "@/test/network";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/endpoints", { spy: true });

afterEach(() => {
  resetOnline();
});

describe("the help dialog offline", () => {
  it("still asks (the worker's cache answers) and says it is the saved copy", async () => {
    setOnline(false);
    vi.mocked(fetchCrisisResources).mockResolvedValue(TEST_RESPONSE);

    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);

    expect(await screen.findByText("Test Crisis Line")).toBeInTheDocument();
    expect(fetchCrisisResources).toHaveBeenCalled();
    expect(screen.getByTestId("crisis-offline-note")).toHaveTextContent(
      "These are the helplines saved on this device",
    );
    expect(screen.getByRole("link", { name: "Call 5550100" })).toHaveAttribute(
      "href",
      "tel:5550100",
    );
  });

  it("with nothing saved yet, says so beside the emergency instruction", async () => {
    setOnline(false);
    vi.mocked(fetchCrisisResources).mockRejectedValue(new TypeError("Failed to fetch"));

    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);

    expect(
      await screen.findByText(
        "You're offline, and the helplines haven't been saved on this device yet.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/call your local emergency number now/i)).toBeInTheDocument();
  });

  it("online, shows no offline note", async () => {
    vi.mocked(fetchCrisisResources).mockResolvedValue(TEST_RESPONSE);
    renderWithProviders(<CrisisResourcesModal onClose={() => undefined} />);
    expect(await screen.findByText("Test Crisis Line")).toBeInTheDocument();
    expect(screen.queryByTestId("crisis-offline-note")).not.toBeInTheDocument();
  });
});
