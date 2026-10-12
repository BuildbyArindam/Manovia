import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  clearJournalDraft,
  loadJournalDraft,
  saveJournalDraft,
} from "@/features/journal/draftStorage";
import { JournalView } from "@/features/journal/JournalView";
import { ApiClient, MemoryTokenStore } from "@/lib/api";

function setNavigatorOnline(online: boolean): void {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    get: () => online,
  });
}

describe("Journal local-only draft persistence", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    setNavigatorOnline(true);
  });

  it("saves, validates, and clears local drafts in localStorage", () => {
    expect(loadJournalDraft()).toBeNull();

    saveJournalDraft({
      activeId: null,
      title: "Quiet morning",
      body: "Keeping this draft on my device until I save.",
      template: "free",
      promptId: null,
      storeEmotions: true,
    });

    const loaded = loadJournalDraft();
    expect(loaded).not.toBeNull();
    expect(loaded?.title).toBe("Quiet morning");
    expect(loaded?.body).toBe("Keeping this draft on my device until I save.");

    clearJournalDraft();
    expect(loadJournalDraft()).toBeNull();
  });

  it("keeps an unsaved journal draft locally when offline and restores it on remount", async () => {
    setNavigatorOnline(false);

    const offlineClient = new ApiClient({
      tokens: new MemoryTokenStore(),
      fetchImpl: async () => {
        throw new TypeError("Failed to fetch");
      },
    });

    const user = userEvent.setup();
    const first = render(<JournalView client={offlineClient} autosaveDelayMs={50} />);

    await user.type(screen.getByTestId("journal-title-input"), "Offline reflection");
    await user.type(
      screen.getByTestId("journal-body-input"),
      "Writing while offline on a train.",
    );

    await waitFor(() => {
      expect(screen.getByTestId("journal-local-draft-badge")).toBeInTheDocument();
    });
    expect(loadJournalDraft()?.body).toBe("Writing while offline on a train.");

    first.unmount();

    render(<JournalView client={offlineClient} autosaveDelayMs={50} />);
    expect(screen.getByTestId("journal-title-input")).toHaveValue("Offline reflection");
    expect(screen.getByTestId("journal-body-input")).toHaveValue(
      "Writing while offline on a train.",
    );

    await user.click(screen.getByTestId("journal-discard-draft"));
    expect(loadJournalDraft()).toBeNull();
  });
});
