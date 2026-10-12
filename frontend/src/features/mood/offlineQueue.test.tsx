import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { beforeEach, describe, expect, it } from "vitest";

import { OfflineBanner } from "@/components/OfflineBanner";
import { MoodView } from "@/features/mood/MoodView";
import { loadOfflineMoodQueue } from "@/features/mood/offlineQueue";
import { makeMoodFake } from "@/test/moodFakes";
import { renderWithProviders } from "@/test/utils";

function setNavigatorOnline(online: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    get: () => online,
  });
}

describe("Offline mood check-in queue and automatic sync", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setNavigatorOnline(true);
  });

  it("queues a mood check-in locally when offline and syncs automatically on reconnect", async () => {
    const user = userEvent.setup();
    const fake = makeMoodFake();

    setNavigatorOnline(false);

    const { container } = renderWithProviders(
      <>
        <OfflineBanner client={fake.client} />
        <MoodView client={fake.client} />
      </>,
    );

    expect(screen.getByTestId("offline-banner")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();

    await user.click(screen.getByRole("radio", { name: "Good" }));
    await user.click(screen.getByRole("checkbox", { name: "Sleep" }));
    await user.type(screen.getByLabelText(/Anything you want to add/i), "Offline evening note");
    await user.click(screen.getByRole("button", { name: "Save check-in" }));

    // Saved locally in the queue, not yet sent to the backend.
    expect(await screen.findByTestId("mood-queued-offline")).toBeInTheDocument();
    expect(fake.sent).toHaveLength(0);
    expect(loadOfflineMoodQueue()).toHaveLength(1);
    expect(screen.getByTestId("offline-queue-count")).toHaveTextContent("1 queued");

    // Shown in the history list with the "Waiting to sync" badge.
    const historyEntry = await screen.findByTestId("mood-history-entry");
    expect(within(historyEntry).getByTestId("mood-queued-badge")).toHaveTextContent(
      "Waiting to sync",
    );
    expect(within(historyEntry).getByText("Offline evening note")).toBeInTheDocument();

    // Reconnect: the online event flushes the queue to the backend.
    await act(async () => {
      setNavigatorOnline(true);
      window.dispatchEvent(new Event("online"));
    });

    await waitFor(() => {
      expect(fake.sent).toHaveLength(1);
    });
    expect(loadOfflineMoodQueue()).toHaveLength(0);
    expect(fake.sent[0]).toMatchObject({
      valence: 4,
      emotions: [],
      factors: ["sleep"],
      note: "Offline evening note",
    });

    await waitFor(() => {
      expect(screen.queryByTestId("mood-queued-badge")).not.toBeInTheDocument();
    });
    expect(await screen.findByTestId("mood-offline-synced")).toHaveTextContent(/Synced 1/);
  });
});
