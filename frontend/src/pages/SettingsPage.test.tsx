import { fireEvent, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { ONBOARDING_STORAGE_KEY } from "@/onboarding/storage";
import { renderWithProviders } from "@/test/utils";
import { SettingsPage } from "./SettingsPage";

const SAVED_SUMMARY = "- I prefer a quiet room when I study.";

function saveSignedInOnboardingRecord(): void {
  window.localStorage.setItem(
    ONBOARDING_STORAGE_KEY,
    JSON.stringify({
      version: 1,
      completedAt: "2026-10-11T00:00:00.000Z",
      consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: true },
      authMode: "guest",
      user: { id: "user-1", email: null, isAnonymous: true },
    }),
  );
}

describe("Settings — saved-chat memory", () => {
  it("shows summaries and confirms irreversible deletion", async () => {
    saveSignedInOnboardingRecord();
    let deleted = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/chat/memory") && method === "DELETE") {
        deleted = true;
        return new Response(JSON.stringify({ deleted_sessions: 1, irreversible: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/chat/memory") && method === "GET") {
        return new Response(
          JSON.stringify({
            memories: deleted ? [] : [{ session_id: "saved-chat-1", summary: SAVED_SUMMARY }],
            max_tokens: 300,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<SettingsPage />);

    expect(await screen.findByText(SAVED_SUMMARY)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete what Manovia remembers" }));
    expect(screen.getByText(/This cannot be undone/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm deletion" }));

    expect(
      await screen.findByText("Nothing is currently remembered from saved chats."),
    ).toBeInTheDocument();
    expect(deleted).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/chat/memory",
      expect.objectContaining({ method: "DELETE" }),
    );
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/was deleted/));
  });
});
