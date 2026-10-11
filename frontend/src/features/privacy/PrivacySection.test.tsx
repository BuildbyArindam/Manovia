/**
 * Settings → Privacy.
 *
 * The assertions that matter are the ones about *behaviour*, not layout: that a
 * toggle sends the patch the backend expects, that the export asks for the ZIP
 * and reports honestly when it cannot be prepared, and that deletion is
 * unreachable until the confirmation phrase is typed — and still asks for a
 * password when the account has one.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ONBOARDING_STORAGE_KEY } from "@/onboarding/storage";
import { renderWithProviders } from "@/test/utils";
import { consentHistory, makePrivacyFake, privacySettings } from "@/test/privacyFakes";

import { PrivacySection } from "./PrivacySection";

describe("PrivacySection", () => {
  // jsdom implements neither of these; the component uses both for a real
  // download, and jsdom's own anchor click tries to navigate the test window.
  const createdUrls: string[] = [];
  let clickedDownloads: string[] = [];
  beforeEach(() => {
    createdUrls.length = 0;
    clickedDownloads = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clickedDownloads.push(this.download);
    });
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => {
        const url = `blob:manovia/${createdUrls.length}`;
        createdUrls.push(url);
        return url;
      }),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("explains what is stored where, in the person's language", async () => {
    const fake = makePrivacyFake();
    renderWithProviders(<PrivacySection client={fake.client} />);

    expect(await screen.findByTestId("privacy-section")).toBeInTheDocument();
    expect(screen.getByText("On this device only")).toBeInTheDocument();
    expect(screen.getByText("On Manovia's server, encrypted")).toBeInTheDocument();
    expect(screen.getByText("Safety events: never your words")).toBeInTheDocument();
    // The anonymous-mode explainer is present, and says what "anonymous" means
    // and does not mean without overclaiming.
    expect(screen.getByRole("heading", { name: "Anonymous mode" })).toBeInTheDocument();
    expect(screen.getByTestId("anonymous-mode-copy")).toHaveTextContent(
      "Anonymous accounts carry no email and no password",
    );
    expect(screen.getByText(/Anonymous does not mean invisible/)).toBeInTheDocument();
  });

  it("shows the stored choices and patches them one at a time", async () => {
    const fake = makePrivacyFake({ settings: privacySettings({ store_chat: false }) });
    const user = userEvent.setup();
    renderWithProviders(<PrivacySection client={fake.client} />);

    const chatToggle = await screen.findByTestId("store-chat-toggle");
    expect(chatToggle).not.toBeChecked();
    expect(screen.getByTestId("store-sentiment-toggle")).toBeChecked();

    await user.click(chatToggle);
    await waitFor(() => {
      expect(fake.patches).toEqual([{ store_chat: true }]);
    });

    await user.click(screen.getByTestId("store-sentiment-toggle"));
    await waitFor(() => {
      expect(fake.patches).toEqual([{ store_chat: true }, { store_sentiment: false }]);
    });
  });

  it("offers exactly the retention windows the server allows", async () => {
    const fake = makePrivacyFake();
    const user = userEvent.setup();
    renderWithProviders(<PrivacySection client={fake.client} />);

    const select = await screen.findByTestId("retention-select");
    const labels = within(select)
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(labels).toEqual([
      "Keep everything until I delete it",
      "Delete anything older than 30 days.",
      "Delete anything older than 3 months.",
      "Delete anything older than 6 months.",
      "Delete anything older than 1 year.",
    ]);

    await user.selectOptions(select, "90");
    await waitFor(() => {
      expect(fake.patches).toEqual([{ retention_days: 90 }]);
    });

    // And back to "forever" must send an explicit null, not omit the field.
    await user.selectOptions(select, "forever");
    await waitFor(() => {
      expect(fake.patches).toEqual([{ retention_days: 90 }, { retention_days: null }]);
    });
  });

  it("reports a failed save with the backend's own code", async () => {
    const fake = makePrivacyFake();
    fake.failSave = { status: 422, code: "empty_update", message: "Nothing to update." };
    const user = userEvent.setup();
    renderWithProviders(<PrivacySection client={fake.client} />);

    await user.click(await screen.findByTestId("store-sentiment-toggle"));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "That choice could not be saved (empty_update). Please try again.",
    );
  });

  it("downloads the export and names the file the server chose", async () => {
    const fake = makePrivacyFake();
    const user = userEvent.setup();
    renderWithProviders(<PrivacySection client={fake.client} />);

    await user.click(await screen.findByTestId("export-button"));

    await waitFor(() => {
      expect(fake.exportDownloads).toBe(1);
    });
    expect(await screen.findByRole("status")).toHaveTextContent("manovia-export-2026-10-11.zip");
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    // The browser was handed the filename the server proposed, not "download".
    expect(clickedDownloads).toEqual(["manovia-export-2026-10-11.zip"]);
  });

  it("says nothing was downloaded when the export fails", async () => {
    const fake = makePrivacyFake();
    fake.failExport = { status: 500, code: "internal_error", message: "Internal server error" };
    const user = userEvent.setup();
    renderWithProviders(<PrivacySection client={fake.client} />);

    await user.click(await screen.findByTestId("export-button"));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "The export could not be prepared (internal_error). Nothing was downloaded.",
    );
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(clickedDownloads).toEqual([]);
  });

  it("lists the agreement history with versions and dates", async () => {
    const fake = makePrivacyFake({ consents: consentHistory() });
    renderWithProviders(<PrivacySection client={fake.client} />);

    const history = await screen.findByTestId("consent-history");
    const rows = within(history).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Terms of use");
    expect(rows[0]).toHaveTextContent("version 2026-10-01");
    expect(rows[2]).toHaveTextContent("About the AI");
  });

  it("keeps deletion unreachable until the phrase is typed exactly", async () => {
    const fake = makePrivacyFake();
    const user = userEvent.setup();
    renderWithProviders(<PrivacySection client={fake.client} />);

    const button = await screen.findByTestId("delete-account-button");
    expect(button).toBeDisabled();

    await user.type(screen.getByTestId("delete-confirmation-input"), "delete");
    await waitFor(() => {
      expect(button).toBeEnabled();
    });

    // A near miss is still a miss.
    await user.clear(screen.getByTestId("delete-confirmation-input"));
    await user.type(screen.getByTestId("delete-confirmation-input"), "DELETE ALL");
    expect(button).toBeDisabled();
    expect(fake.lastDeleteBody).toBeNull();
  });

  it("sends the confirmation phrase and renders the receipt", async () => {
    const fake = makePrivacyFake();
    const user = userEvent.setup();
    const onDeleted = vi.fn();
    renderWithProviders(<PrivacySection client={fake.client} onDeleted={onDeleted} />);

    await user.type(await screen.findByTestId("delete-confirmation-input"), "DELETE");
    await user.click(screen.getByTestId("delete-account-button"));

    const receipt = await screen.findByTestId("deletion-receipt");
    expect(receipt).toHaveTextContent("12 rows removed");
    expect(receipt).toHaveTextContent("2 × journal_entries");
    // The account-reference explanation is part of the receipt, not a tooltip.
    expect(receipt).toHaveTextContent("a one-way hash, not your account number");
    expect(fake.lastDeleteBody).toEqual({ confirm: "DELETE" });
    expect(onDeleted).toHaveBeenCalledTimes(1);
  });

  it("curates each deletion failure instead of echoing the message", async () => {
    for (const [code, expected] of [
      ["confirmation_required", "The confirmation phrase did not match. Nothing was deleted."],
      ["reauth_required", "Enter your password to confirm. Nothing was deleted."],
      ["invalid_credentials", "That password was not right. Nothing was deleted."],
      ["account_locked", "Too many wrong passwords, so deletion is paused for a while."],
    ] as const) {
      const fake = makePrivacyFake();
      fake.failDelete = { status: 403, code, message: "backend text that must not be shown" };
      const user = userEvent.setup();
      const { unmount } = renderWithProviders(<PrivacySection client={fake.client} />);

      await user.type(await screen.findByTestId("delete-confirmation-input"), "DELETE");
      await user.click(screen.getByTestId("delete-account-button"));

      const status = await screen.findByRole("status");
      expect(status.textContent).toContain(expected);
      expect(status.textContent).not.toContain("backend text that must not be shown");
      unmount();
    }
  });

  it("asks a password account for its password", async () => {
    // The onboarding record is read once, when the provider mounts, so it has
    // to be in storage before the render.
    window.localStorage.setItem(
      ONBOARDING_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        completedAt: "2026-10-11T09:00:00Z",
        consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: true },
        authMode: "account",
        user: { id: "u1", email: "someone@example.test", isAnonymous: false },
      }),
    );
    try {
      const fake = makePrivacyFake();
      const user = userEvent.setup();
      renderWithProviders(<PrivacySection client={fake.client} />);

      expect(await screen.findByTestId("delete-password-input")).toBeInTheDocument();
      await user.type(screen.getByTestId("delete-confirmation-input"), "DELETE");
      await user.type(screen.getByTestId("delete-password-input"), "correct-horse-battery");
      await user.click(screen.getByTestId("delete-account-button"));

      await waitFor(() => {
        expect(fake.lastDeleteBody).toEqual({
          confirm: "DELETE",
          password: "correct-horse-battery",
        });
      });
    } finally {
      window.localStorage.removeItem(ONBOARDING_STORAGE_KEY);
    }
  });

  it("does not ask an anonymous account for a password, and names the trade-off", async () => {
    window.localStorage.setItem(
      ONBOARDING_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        completedAt: "2026-10-11T09:00:00Z",
        consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: false },
        authMode: "guest",
        user: { id: "u2", email: null, isAnonymous: true },
      }),
    );
    try {
      const fake = makePrivacyFake();
      const user = userEvent.setup();
      renderWithProviders(<PrivacySection client={fake.client} />);

      expect(await screen.findByTestId("privacy-section")).toBeInTheDocument();
      expect(screen.queryByTestId("delete-password-input")).not.toBeInTheDocument();
      // The anonymous branch explains the real trade-off: no recovery.
      expect(screen.getByTestId("anonymous-mode-copy")).toHaveTextContent(
        "nobody, including you, can recover it if this browser forgets its tokens",
      );

      await user.type(screen.getByTestId("delete-confirmation-input"), "DELETE");
      await user.click(screen.getByTestId("delete-account-button"));

      await waitFor(() => {
        expect(fake.lastDeleteBody).toEqual({ confirm: "DELETE" });
      });
    } finally {
      window.localStorage.removeItem(ONBOARDING_STORAGE_KEY);
    }
  });
});
