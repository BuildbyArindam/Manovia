/**
 * The owner's links: the audit sentence, the history, and the two ways to end
 * them.
 *
 * The number on screen is the number the server counted — the test asserts the
 * sentence comes from the response rather than being recomputed here, because
 * "opened 2 times" is the whole point of the audit log and a UI that derives its
 * own count could disagree with it.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/utils";
import { auditOut, makeShareFake, shareSummary } from "@/test/sharingFakes";

import { REVOKE_ALL_CONFIRMATION, ShareLinks } from "./ShareLinks";

const SHARE_ID = "9c1f0d5e-9c3a-4a1e-9f2b-6d0c1e2f3a4c";

describe("ShareLinks", () => {
  it("shows the server's own audit sentence for each link", async () => {
    const fake = makeShareFake({
      shares: [
        shareSummary(),
        shareSummary({
          id: "11111111-2222-3333-4444-555555555555",
          status: "expired",
          summary: "Opened 0 times of 5 allowed. Expired — it no longer works.",
          token_prefix: "abcdefgh",
        }),
      ],
    });
    renderWithProviders(<ShareLinks client={fake.client} />);

    const row = await screen.findByTestId(`share-row-${SHARE_ID}`);
    expect(within(row).getByTestId(`share-summary-${SHARE_ID}`)).toHaveTextContent(
      "Opened 2 times of 5 allowed.",
    );
    expect(within(row).getByTestId(`share-status-${SHARE_ID}`)).toHaveTextContent("Active");

    const expired = screen.getByTestId("share-row-11111111-2222-3333-4444-555555555555");
    expect(
      within(expired).getByTestId(`share-status-${"11111111-2222-3333-4444-555555555555"}`),
    ).toHaveTextContent("Expired");
    expect(within(expired).getByText(/Expired — it no longer works/)).toBeInTheDocument();
  });

  it("says when there is nothing to look at", async () => {
    const fake = makeShareFake({ shares: [] });
    renderWithProviders(<ShareLinks client={fake.client} />);

    expect(await screen.findByTestId("share-list-empty")).toHaveTextContent("No links yet.");
    expect(screen.getByTestId("share-revoke-all")).toBeDisabled();
  });

  it("opens the audit history and names each event and who did it", async () => {
    const fake = makeShareFake({ audit: auditOut() });
    const user = userEvent.setup();
    renderWithProviders(<ShareLinks client={fake.client} />);

    const button = await screen.findByTestId(`share-audit-${SHARE_ID}`);
    expect(button).toHaveAttribute("aria-expanded", "false");
    await user.click(button);

    const panel = await screen.findByTestId("share-audit");
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(within(panel).getByTestId("share-audit-summary")).toHaveTextContent(
      "Opened 2 times of 5 allowed.",
    );
    const events = within(panel).getAllByTestId("share-audit-event");
    expect(events).toHaveLength(4);
    expect(events[0]).toHaveTextContent("created");
    expect(events[0]).toHaveTextContent("you");
    expect(events[1]).toHaveTextContent("opened");
    expect(events[1]).toHaveTextContent("the person you shared with");
    expect(events[2]).toHaveTextContent("wrong passcode");
    // The audit trail keeps no free text: kind, actor and time are all there is.
    expect(panel.textContent).not.toContain("ciphertext");
  });

  it("revokes one link and shows it as revoked", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(<ShareLinks client={fake.client} />);

    const revoke = await screen.findByTestId(`share-revoke-${SHARE_ID}`);
    expect(revoke).toBeEnabled();
    await user.click(revoke);

    await waitFor(() => {
      expect(fake.revokes).toEqual([SHARE_ID]);
    });
    await waitFor(() => {
      expect(screen.getByTestId(`share-status-${SHARE_ID}`)).toHaveTextContent("Revoked");
    });
    // A dead link cannot be revoked twice.
    expect(screen.getByTestId(`share-revoke-${SHARE_ID}`)).toBeDisabled();
  });

  it("leaves an already-dead link alone", async () => {
    const fake = makeShareFake({ shares: [shareSummary({ status: "revoked" })] });
    renderWithProviders(<ShareLinks client={fake.client} />);

    const revoke = await screen.findByTestId(`share-revoke-${SHARE_ID}`);
    expect(revoke).toBeDisabled();
  });

  it("needs the typed word before revoking everything", async () => {
    const fake = makeShareFake({
      shares: [shareSummary(), shareSummary({ id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" })],
    });
    const user = userEvent.setup();
    renderWithProviders(<ShareLinks client={fake.client} />);

    const revokeAll = await screen.findByTestId("share-revoke-all");
    expect(revokeAll).toBeDisabled();

    await user.type(screen.getByTestId("share-revoke-all-input"), "revoke");
    expect(revokeAll).toBeDisabled();

    await user.clear(screen.getByTestId("share-revoke-all-input"));
    await user.type(screen.getByTestId("share-revoke-all-input"), REVOKE_ALL_CONFIRMATION);
    expect(revokeAll).toBeEnabled();

    await user.click(revokeAll);
    await waitFor(() => {
      expect(fake.revokeAllCalls).toBe(1);
    });
    expect(await screen.findByTestId("share-revoked-notice")).toHaveTextContent("Revoked 2");
    await waitFor(() => {
      expect(screen.getByTestId(`share-status-${SHARE_ID}`)).toHaveTextContent("Revoked");
    });
    // The phrase is cleared, so the button cannot be clicked twice by accident.
    expect(screen.getByTestId("share-revoke-all-input")).toHaveValue("");
    expect(revokeAll).toBeDisabled();
  });

  it("warns that a locked link is locked, and until when", async () => {
    const fake = makeShareFake({
      shares: [shareSummary({ locked_until: "2026-10-19T14:00:00Z", passcode_failures: 5 })],
    });
    renderWithProviders(<ShareLinks client={fake.client} />);

    const row = await screen.findByTestId(`share-row-${SHARE_ID}`);
    expect(within(row).getByTestId(`share-locked-${SHARE_ID}`)).toHaveTextContent(/Locked until/);
  });
});
