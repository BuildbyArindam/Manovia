/**
 * "Prepare for my appointment".
 *
 * The assertions here are about the promise the screen makes: nothing is ticked
 * by default, the preview is what gets created (the same hash travels from one
 * request to the next), and the key appears exactly once with the warning that
 * it cannot be fetched again. A test that only checked "the checkboxes render"
 * would pass for a builder that shares the wrong thing.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";
import {
  makeShareFake,
  previewOut,
  shareOptions,
  VECTOR_SECRET,
  VECTOR_TOKEN,
} from "@/test/sharingFakes";

import { ShareBuilder } from "./ShareBuilder";

/**
 * Longer than the builder's 400 ms preview debounce.
 *
 * This is a *waitFor* options object, so it goes in find*'s THIRD argument —
 * the second is the matcher-options slot, where `{ timeout }` is not a known
 * property. Vitest strips types (esbuild), so putting it second keeps the suite
 * green and breaks `npm run typecheck`; `tsc` is what catches it.
 */
const PREVIEW_WAIT = { timeout: 2000 };

async function tick(user: ReturnType<typeof userEvent.setup>, key: string): Promise<void> {
  await user.click(await screen.findByTestId(`share-item-${key}`, undefined, PREVIEW_WAIT));
}

describe("ShareBuilder", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("starts with nothing ticked and shows the catalogue the server sent", async () => {
    const fake = makeShareFake();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    const builder = await screen.findByTestId("share-builder", undefined, PREVIEW_WAIT);
    expect(builder).toBeInTheDocument();

    expect(screen.getByText("Mood trend summary")).toBeInTheDocument();
    expect(screen.getByText("PHQ-9 and GAD-7 history")).toBeInTheDocument();
    expect(screen.getByText("Selected thought records")).toBeInTheDocument();

    for (const key of ["mood_trend", "assessments", "thought_records", "note", "questions"]) {
      expect(screen.getByTestId(`share-item-${key}`)).not.toBeChecked();
    }
    // The exclusion sentences are the comprehension part: they are on screen.
    expect(screen.getByText(/your answers to the individual questions/)).toBeInTheDocument();
    expect(screen.getByText(/records you did not tick/)).toBeInTheDocument();
    // And the preview says there is nothing to show yet.
    expect(screen.getByTestId("share-preview-empty")).toBeInTheDocument();
    expect(fake.previews).toEqual([]);
  });

  it("keeps the builder behind the switch, and turns sharing on when asked", async () => {
    const fake = makeShareFake({ options: shareOptions({ enabled: false }) });
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    expect(await screen.findByTestId("share-off", undefined, PREVIEW_WAIT)).toBeInTheDocument();
    expect(screen.getByText(/Nothing can be shared until you switch this on/)).toBeInTheDocument();
    expect(screen.queryByTestId("share-builder")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("share-enable-toggle"));
    await waitFor(() => {
      expect(fake.preferences).toEqual([true]);
    });
    expect(await screen.findByTestId("share-builder", undefined, PREVIEW_WAIT)).toBeInTheDocument();
  });

  it("says so when the deployment has sharing switched off", async () => {
    const fake = makeShareFake({
      options: shareOptions({ enabled: false, deployment_enabled: false }),
    });
    renderWithProviders(<ShareBuilder client={fake.client} />);

    expect(
      await screen.findByTestId("share-deployment-off", undefined, PREVIEW_WAIT),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("share-enable-toggle")).not.toBeInTheDocument();
  });

  it("previews exactly the selection, and only after the selection settles", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "note");
    await user.type(
      await screen.findByTestId("share-note", undefined, PREVIEW_WAIT),
      "I sleep badly",
    );

    const preview = await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);
    expect(within(preview).getByText(/Identifies you as/)).toBeInTheDocument();
    expect(screen.getByTestId("share-preview-hashes")).toHaveTextContent("consent aaaaaaaaaaaa");

    // One preview per settled selection, carrying exactly what was ticked.
    expect(fake.previews.length).toBeGreaterThan(0);
    const last = fake.previews[fake.previews.length - 1];
    expect(last).toBeDefined();
    expect(last?.items).toEqual(["note"]);
    expect(last?.note).toBe("I sleep badly");
    expect(last?.window_days).toBe(30);
    expect(last?.questions).toBeUndefined();
    expect(last?.thought_record_ids).toBeUndefined();
  });

  it("leaves an unticked item out of the request even when its field has text", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "note");
    await tick(user, "questions");
    await user.type(
      await screen.findByTestId("share-note", undefined, PREVIEW_WAIT),
      "written down",
    );
    await user.click(screen.getByTestId("share-question-add"));
    await user.type(screen.getByTestId("share-question-0"), "Is this normal?");
    await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);

    // Untick the note: its text stays in the field but must leave the request.
    await user.click(screen.getByTestId("share-item-note"));
    await waitFor(() => {
      const last = fake.previews[fake.previews.length - 1];
      expect(last?.items).toEqual(["questions"]);
      expect(last?.note).toBeUndefined();
      expect(last?.questions).toEqual(["Is this normal?"]);
    });
    // Unticking hides the field but does not delete what was written: ticking it
    // again brings the words back, so a mis-click cannot cost somebody their note.
    await user.click(screen.getByTestId("share-item-note"));
    expect(await screen.findByTestId("share-note", undefined, PREVIEW_WAIT)).toHaveValue(
      "written down",
    );
    await waitFor(() => {
      const last = fake.previews[fake.previews.length - 1];
      expect(last?.note).toBe("written down");
    });
  });

  it("sends the record ids it was given, and stops at the server's limit", async () => {
    const fake = makeShareFake({
      options: shareOptions({
        thought_records: [
          {
            id: "record-1",
            created_at: "2026-10-16T18:30:00Z",
            emotion: "anxiety",
            intensity_before: 80,
            intensity_after: 45,
          },
          {
            id: "record-2",
            created_at: "2026-10-15T09:00:00Z",
            emotion: "low",
            intensity_before: 70,
            intensity_after: 60,
          },
        ],
        limits: {
          note_chars: 2000,
          questions: 10,
          question_chars: 300,
          thought_records: 1,
          windows: [14, 30, 90],
        },
      }),
    });
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "thought_records");
    const panel = await screen.findByTestId("share-records", undefined, PREVIEW_WAIT);
    // Metadata only: the record's text was never sent to the browser.
    expect(within(panel).getByText(/anxiety/)).toBeInTheDocument();
    expect(within(panel).getByText(/80 to 45/)).toBeInTheDocument();

    await user.click(screen.getByTestId("share-record-record-1"));
    expect(screen.getByTestId("share-record-record-2")).toBeDisabled();
    expect(within(panel).getByText("1 of 1 chosen")).toBeInTheDocument();

    await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);
    await waitFor(() => {
      const last = fake.previews[fake.previews.length - 1];
      expect(last?.thought_record_ids).toEqual(["record-1"]);
    });
  });

  it("collects questions, drops the empty ones, and caps the list", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "questions");
    await user.click(await screen.findByTestId("share-question-add", undefined, PREVIEW_WAIT));
    await user.type(screen.getByTestId("share-question-0"), "Is this normal?");
    await user.click(screen.getByTestId("share-question-add"));
    await user.type(screen.getByTestId("share-question-1"), "  ");
    await user.click(screen.getByTestId("share-question-add"));
    await user.type(screen.getByTestId("share-question-2"), "What next?");
    await user.click(screen.getByTestId("share-question-remove-1"));

    await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);
    await waitFor(() => {
      const last = fake.previews[fake.previews.length - 1];
      expect(last?.questions).toEqual(["Is this normal?", "What next?"]);
    });
    expect(screen.getByTestId("share-question-1")).toHaveValue("What next?");
  });

  it("refuses to create until the person has read the preview and confirmed", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    expect(await screen.findByTestId("share-create", undefined, PREVIEW_WAIT)).toBeDisabled();

    await tick(user, "note");
    await user.type(
      await screen.findByTestId("share-note", undefined, PREVIEW_WAIT),
      "For my appointment",
    );
    await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);

    // A preview alone is not consent.
    expect(screen.getByTestId("share-create")).toBeDisabled();
    expect(screen.getByTestId("share-confirm")).toBeEnabled();

    await user.click(screen.getByTestId("share-confirm"));
    await user.click(screen.getByTestId("share-create"));

    await waitFor(() => {
      expect(fake.creates.length).toBe(1);
    });
    const body = fake.creates[0];
    expect(body?.consent_sha256).toBe("a".repeat(64));
    expect(body?.items).toEqual(["note"]);
    expect(body?.formats).toEqual(["pdf"]);
    expect(body?.expires_in_days).toBe(7);
    expect(body?.view_limit).toBe(5);
    expect(body?.passcode).toBeNull();
  });

  it("shows the link once, with the key in the fragment and the warning next to it", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "note");
    await user.type(
      await screen.findByTestId("share-note", undefined, PREVIEW_WAIT),
      "For my appointment",
    );
    await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);
    await user.click(screen.getByTestId("share-confirm"));
    await user.click(screen.getByTestId("share-create"));

    const panel = await screen.findByTestId("share-created", undefined, PREVIEW_WAIT);
    const link = within(panel).getByTestId("share-created-link");
    expect(link).toHaveTextContent(`/s/${VECTOR_TOKEN}#k=${VECTOR_SECRET}`);
    // The key is in the fragment, never in the path or the query.
    expect(link.textContent?.split("#")[0]).toBe(`${window.location.origin}/s/${VECTOR_TOKEN}`);
    expect(within(panel).getByTestId("share-created-passcode")).toHaveTextContent("483920");
    expect(within(panel).getByTestId("share-created-warning")).toHaveTextContent(
      /not stored anywhere/,
    );
    expect(within(panel).getByTestId("share-created-documents")).toHaveTextContent("pdf");
    expect(within(panel).getByTestId("share-created-documents")).toHaveTextContent("encrypted");
    expect(within(panel).getByTestId("share-created-consent")).toHaveTextContent(
      "Identifies you as",
    );
    // The builder is behind the panel now: the key is the thing to look at.
    expect(screen.queryByTestId("share-confirm")).not.toBeInTheDocument();
  });

  it("copies the link and the passcode to the clipboard", async () => {
    const fake = makeShareFake();
    // user-event installs its own clipboard stub, so the copy is read back from
    // there rather than from a hand-made mock it would overwrite.
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "note");
    await user.type(
      await screen.findByTestId("share-note", undefined, PREVIEW_WAIT),
      "For my appointment",
    );
    await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);
    await user.click(screen.getByTestId("share-confirm"));
    await user.click(screen.getByTestId("share-create"));

    const panel = await screen.findByTestId("share-created", undefined, PREVIEW_WAIT);
    await user.click(within(panel).getByTestId("share-copy-link"));
    await waitFor(() => {
      expect(within(panel).getByTestId("share-copy-link")).toHaveTextContent("Copied");
    });
    expect(await navigator.clipboard.readText()).toContain(`/s/${VECTOR_TOKEN}#k=${VECTOR_SECRET}`);

    await user.click(within(panel).getByTestId("share-copy-passcode"));
    await waitFor(async () => {
      expect(await navigator.clipboard.readText()).toBe("483920");
    });
    expect(within(panel).getByTestId("share-copy-passcode")).toHaveTextContent("Copied");
  });

  it("takes the link settings the person chose", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "mood_trend");
    await user.click(await screen.findByTestId("share-format-fhir", undefined, PREVIEW_WAIT));
    await user.selectOptions(screen.getByTestId("share-window"), "90");
    await user.clear(screen.getByTestId("share-expires"));
    await user.type(screen.getByTestId("share-expires"), "14");
    await user.clear(screen.getByTestId("share-views"));
    await user.type(screen.getByTestId("share-views"), "2");
    await user.type(screen.getByTestId("share-passcode"), "hunter22");

    await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);
    await user.click(screen.getByTestId("share-confirm"));
    await user.click(screen.getByTestId("share-create"));

    await waitFor(() => {
      expect(fake.creates.length).toBe(1);
    });
    const body = fake.creates[0];
    expect(body?.formats).toEqual(["pdf", "fhir"]);
    expect(body?.window_days).toBe(90);
    expect(body?.expires_in_days).toBe(14);
    expect(body?.view_limit).toBe(2);
    expect(body?.passcode).toBe("hunter22");
  });

  it("explains a consent mismatch and throws the stale preview away", async () => {
    const fake = makeShareFake();
    fake.failCreate = {
      status: 409,
      code: "consent_mismatch",
      message: "What would be shared has changed since you looked.",
    };
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "note");
    await user.type(
      await screen.findByTestId("share-note", undefined, PREVIEW_WAIT),
      "For my appointment",
    );
    await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);
    await user.click(screen.getByTestId("share-confirm"));
    await user.click(screen.getByTestId("share-create"));

    expect(
      await screen.findByTestId("share-create-error", undefined, PREVIEW_WAIT),
    ).toHaveTextContent(/changed since the preview/);
    expect(screen.queryByTestId("share-created")).not.toBeInTheDocument();
  });

  it("explains the other refusals in plain words", async () => {
    const cases: ReadonlyArray<{
      code: string;
      status: number;
      message: string;
      expect: RegExp;
    }> = [
      {
        code: "share_limit",
        status: 409,
        // The server quotes the deployment's cap; the UI keeps the number.
        message: "You already have 10 live links.",
        expect: /already have 10 live links/,
      },
      {
        code: "share_limit",
        status: 409,
        // No number to keep: the server's own sentence is shown rather than a
        // translated one with a hole in it.
        message: "Too many live links.",
        expect: /Too many live links\./,
      },
      {
        code: "sharing_disabled",
        status: 403,
        message: "server says no",
        expect: /Sharing is switched off/,
      },
      {
        code: "share_request_invalid",
        status: 422,
        message: "server says no",
        expect: /Check the selection/,
      },
    ];
    for (const spec of cases) {
      const fake = makeShareFake();
      fake.failCreate = { status: spec.status, code: spec.code, message: spec.message };
      const user = userEvent.setup();
      const view = renderWithProviders(<ShareBuilder client={fake.client} />);

      await tick(user, "note");
      await user.type(await screen.findByTestId("share-note", undefined, PREVIEW_WAIT), "hello");
      await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);
      await user.click(screen.getByTestId("share-confirm"));
      await user.click(screen.getByTestId("share-create"));

      expect(
        await screen.findByTestId("share-create-error", undefined, PREVIEW_WAIT),
      ).toHaveTextContent(spec.expect);
      view.unmount();
    }
  });

  it("shows a preview failure instead of silently offering an empty document", async () => {
    const fake = makeShareFake();
    fake.failWith = { status: 422, code: "share_request_invalid", message: "no items" };
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "note");
    expect(
      await screen.findByTestId("share-preview-error", undefined, PREVIEW_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByTestId("share-confirm")).toBeDisabled();
    expect(screen.getByTestId("share-create")).toBeDisabled();
  });

  it("renders the preview the server built, not a copy the UI invented", async () => {
    const fake = makeShareFake({
      preview: previewOut({
        consent_lines: [
          "Mood summary: 3 check-ins over 14 days.",
          "Never included: chat conversations with Manovia.",
        ],
      }),
    });
    const user = userEvent.setup();
    renderWithProviders(<ShareBuilder client={fake.client} />);

    await tick(user, "mood_trend");
    const preview = await screen.findByTestId("share-preview", undefined, PREVIEW_WAIT);
    expect(within(preview).getAllByRole("listitem")).toHaveLength(2);
    expect(
      within(preview).getByText("Mood summary: 3 check-ins over 14 days."),
    ).toBeInTheDocument();
  });
});
