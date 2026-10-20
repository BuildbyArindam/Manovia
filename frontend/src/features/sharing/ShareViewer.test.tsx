/**
 * The public viewer, end to end — including the real decryption.
 *
 * The happy path here is not a mock: the fake server hands back ciphertext that
 * `app/services/sharing/crypto.py` produced from a full share payload and a real
 * PDF, and this component derives the key in the browser and decrypts it. So
 * these tests fail if the HKDF parameters, the AAD, the base64url alphabet or
 * the payload shape drift between the two languages.
 *
 * The refusal tests check the other half of the promise: a dead link gets one
 * short answer, and a document that will not open does not tell the viewer
 * *why* — because "wrong passcode" versus "wrong key" versus "somebody edited
 * this" is exactly the distinction an attacker would probe for.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";
import {
  DISCLAIMER_TITLE,
  VECTOR_PASSCODE,
  VECTOR_SECRET,
  VECTOR_TOKEN,
  makeShareFake,
  openOut,
  publicStatus,
} from "@/test/sharingFakes";
import VECTOR from "@/features/sharing/testSupport/shareVector.json";
import type { SharePayload } from "./types";
import { toBase64Url } from "./crypto";

import { ShareViewer } from "./ShareViewer";

/** The document the committed ciphertext decrypts to: asserted, not retyped. */
const PAYLOAD = VECTOR.payload as SharePayload;
const RECORD = PAYLOAD.thought_records?.[0];

/** A different, well-formed key: the "somebody forwarded the link" case. */
const WRONG_SECRET = toBase64Url(new Uint8Array(32).fill(9));

/**
 * waitFor options for the open/decrypt round trip, which runs the real HKDF and
 * AES-GCM in jsdom before React can render the document. Passed as find*'s
 * THIRD argument: the second is the matcher-options slot, and a `timeout` there
 * is a type error that only `tsc` reports (vitest strips types).
 */
const OPEN_WAIT = { timeout: 3000 };

describe("ShareViewer", () => {
  const createdUrls: string[] = [];
  beforeEach(() => {
    createdUrls.length = 0;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => {
        const url = `blob:manovia/${createdUrls.length}`;
        createdUrls.push(url);
        return url;
      }),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    window.print = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("refuses a dead link with one short answer and spends nothing", async () => {
    const fake = makeShareFake();
    fake.failPublic = {
      status: 404,
      code: "share_unavailable",
      message: "This link is not available.",
    };
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    const refusal = await screen.findByTestId("share-viewer-refused");
    expect(refusal).toHaveTextContent("This link is not available");
    expect(refusal).toHaveTextContent(/expired, been revoked, or reached its opening limit/);
    expect(screen.queryByTestId("share-viewer-passcode")).not.toBeInTheDocument();
    expect(fake.opens).toBe(0);
  });

  it("refuses a link the server says is not available, without asking why", async () => {
    const fake = makeShareFake();
    fake.status = publicStatus({ available: false });
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    expect(await screen.findByTestId("share-viewer-refused")).toHaveTextContent(
      "This link is not available",
    );
    expect(fake.opens).toBe(0);
  });

  it("says when the link arrived without its key, before taking a passcode", async () => {
    const fake = makeShareFake();
    renderWithProviders(<ShareViewer token={VECTOR_TOKEN} urlKey={null} client={fake.client} />);

    const refusal = await screen.findByTestId("share-viewer-refused");
    expect(refusal).toHaveTextContent("This link is missing its key");
    expect(refusal).toHaveTextContent(/after the #/);
    expect(screen.queryByTestId("share-viewer-passcode")).not.toBeInTheDocument();
    expect(fake.opens).toBe(0);
  });

  it("shows the banner before the passcode field, and warns that opening spends a view", async () => {
    const fake = makeShareFake();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    const form = await screen.findByTestId("share-viewer-form");
    expect(within(form).getByTestId("share-banner-title")).toHaveTextContent(DISCLAIMER_TITLE);
    expect(within(form).getByTestId("share-banner-body")).toHaveTextContent(/not a diagnosis/);
    expect(within(form).getByTestId("share-viewer-spends")).toHaveTextContent(
      /spends one of this link's openings/,
    );
    const passcode = within(form).getByTestId("share-viewer-passcode");
    expect(passcode).toHaveAttribute("type", "password");
    expect(passcode).toHaveAccessibleName("Passcode");
    expect(within(form).getByTestId("share-viewer-submit")).toBeDisabled();
    expect(fake.opens).toBe(0);
  });

  it("decrypts a real share with the key from the fragment and renders every item", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), VECTOR_PASSCODE);
    await user.click(screen.getByTestId("share-viewer-submit"));

    const opened = await screen.findByTestId("share-viewer-open", undefined, OPEN_WAIT);
    expect(fake.opens).toBe(1);
    expect(fake.passcodes).toEqual([VECTOR_PASSCODE]);

    // The banner is the document's own, not this page's copy.
    expect(within(opened).getByTestId("share-banner-title")).toHaveTextContent(DISCLAIMER_TITLE);
    expect(within(opened).getByTestId("share-banner-body")).toHaveTextContent(
      /not a diagnosis, not a clinical assessment/,
    );

    // Mood summary: numbers only.
    const mood = within(opened).getByTestId("share-mood");
    expect(mood).toHaveTextContent(String(PAYLOAD.mood_trend?.entries ?? 0));
    expect(within(opened).getByTestId("share-mood-direction")).toHaveTextContent(
      /second half of this window were higher/,
    );
    expect(within(opened).getByTestId("share-mood-emotions")).toHaveTextContent("calm (3)");

    // Screenings: the verified LOINC codes, linked to the pages they came from.
    const rows = within(opened).getAllByTestId("share-assessment-row");
    expect(rows).toHaveLength(2);
    expect(within(opened).getByTestId("share-assessments")).toHaveTextContent("44261-6");
    expect(within(opened).getByTestId("share-assessments")).toHaveTextContent("70274-6");
    const links = within(opened).getAllByTestId("share-loinc-link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "https://loinc.org/70274-6",
      "https://loinc.org/44261-6",
    ]);
    expect(within(opened).getByTestId("share-assessments")).toHaveTextContent("not a diagnosis");

    // The thought record, in full, under the headings it was written with.
    expect(RECORD).toBeDefined();
    expect(within(opened).getByTestId("share-record-situation")).toHaveTextContent(
      RECORD?.situation ?? "",
    );
    expect(within(opened).getByTestId("share-record-automatic_thought")).toHaveTextContent(
      RECORD?.automatic_thought ?? "",
    );
    expect(within(opened).getByTestId("share-record-evidence_for")).toHaveTextContent(
      RECORD?.evidence_for ?? "",
    );
    expect(within(opened).getByTestId("share-record-evidence_against")).toHaveTextContent(
      RECORD?.evidence_against ?? "",
    );
    expect(within(opened).getByTestId("share-record-balanced_thought")).toHaveTextContent(
      RECORD?.balanced_thought ?? "",
    );
    expect(within(opened).getByTestId("share-record-helped")).toHaveTextContent(
      (RECORD?.what_helped ?? []).join(", "),
    );

    // Exercise helpfulness, with the caveat that a moved rating is not proof.
    expect(within(opened).getByTestId("share-exercise")).toHaveTextContent("Box breathing");
    expect(within(opened).getByTestId("share-exercise-scale")).toHaveTextContent(
      /association, not proof/,
    );

    // Their words, verbatim.
    expect(within(opened).getByTestId("share-note-text")).toHaveTextContent(PAYLOAD.note ?? "");
    const questions = within(opened).getByTestId("share-questions");
    for (const question of PAYLOAD.questions ?? []) {
      expect(questions).toHaveTextContent(question);
    }

    // The budget and the expiry, as the server reported them.
    expect(within(opened).getByTestId("share-viewer-views")).toHaveTextContent("4");
    expect(within(opened).getByTestId("share-viewer-expires")).toBeInTheDocument();
    expect(within(opened).getByTestId("share-viewer-footer")).toHaveTextContent(
      /not an electronic health record and not a crisis service/,
    );
  });

  it("offers the decrypted documents as downloads, with honest filenames", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), VECTOR_PASSCODE);
    await user.click(screen.getByTestId("share-viewer-submit"));

    const downloads = await screen.findByTestId("share-viewer-downloads", undefined, OPEN_WAIT);
    const pdf = within(downloads).getByTestId("share-download-pdf");
    expect(pdf).toHaveAttribute("download", "manovia-appointment-summary.pdf");
    expect(pdf).toHaveAttribute("href", "blob:manovia/1");
    expect(pdf).toHaveTextContent("Download the PDF");
    expect(within(downloads).getByTestId("share-download-payload")).toHaveAttribute(
      "download",
      "manovia-share-summary.json",
    );
    expect(createdUrls).toHaveLength(2);

    await user.click(within(downloads).getByTestId("share-viewer-print"));
    expect(window.print).toHaveBeenCalled();
    // Printing and downloading do not spend another view.
    expect(fake.opens).toBe(1);
  });

  it("spends one view per opening, and leaves nothing to submit twice", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), VECTOR_PASSCODE);
    await user.click(screen.getByTestId("share-viewer-submit"));
    await screen.findByTestId("share-viewer-open", undefined, OPEN_WAIT);

    // The form is replaced by the document, so there is nothing left to submit.
    expect(screen.queryByTestId("share-viewer-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("share-viewer-submit")).not.toBeInTheDocument();
    expect(fake.opens).toBe(1);

    // Reading it again — printing, downloading, scrolling — costs nothing more.
    await user.click(screen.getByTestId("share-download-pdf"));
    await user.click(screen.getByTestId("share-viewer-print"));
    expect(fake.opens).toBe(1);
    expect(fake.passcodes).toEqual([VECTOR_PASSCODE]);
  });

  it("reports a wrong passcode with the number of tries left", async () => {
    const fake = makeShareFake();
    fake.failOpen = {
      status: 403,
      code: "passcode_incorrect",
      message: "That passcode is not right. 3 tries left before this link locks.",
    };
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), "000000");
    await user.click(screen.getByTestId("share-viewer-submit"));

    const error = await screen.findByTestId("share-viewer-error");
    await waitFor(() => {
      expect(error).toHaveTextContent("3 attempt(s) left");
    });
    expect(screen.getByTestId("share-viewer-submit")).toBeEnabled();
    expect(screen.getByTestId("share-viewer-form")).toBeInTheDocument();
  });

  it("reports a lockout in minutes", async () => {
    const fake = makeShareFake();
    fake.failOpen = {
      status: 423,
      code: "passcode_locked",
      message: "Too many wrong passcodes. This link is locked for about 15 minutes.",
    };
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), "000000");
    await user.click(screen.getByTestId("share-viewer-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("share-viewer-error")).toHaveTextContent("15 minute(s)");
    });
  });

  it("reports a throttle without naming the link", async () => {
    const fake = makeShareFake();
    fake.failOpen = {
      status: 429,
      code: "rate_limited",
      message: "Too many attempts. Please wait a minute and try again.",
    };
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), VECTOR_PASSCODE);
    await user.click(screen.getByTestId("share-viewer-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("share-viewer-error")).toHaveTextContent(
        "Too many attempts from here",
      );
    });
  });

  it("does not say which half of the link was wrong when it will not open", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={WRONG_SECRET} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), VECTOR_PASSCODE);
    await user.click(screen.getByTestId("share-viewer-submit"));

    const refusal = await screen.findByTestId("share-viewer-refused", undefined, OPEN_WAIT);
    expect(refusal).toHaveTextContent("The document did not open with this key and passcode");
    expect(refusal).toHaveTextContent(/cannot be fetched again|never stored/);
    // One answer for a wrong key, a wrong passcode and a changed byte.
    const lowered = (refusal.textContent ?? "").toLowerCase();
    expect(lowered).not.toContain("wrong passcode");
    expect(lowered).not.toContain("tampered");
    expect(lowered).not.toContain("wrong key");
    // The view was spent, and the page says what to do next.
    expect(fake.opens).toBe(1);
    expect(screen.getByTestId("share-viewer-retry")).toBeInTheDocument();
  });

  it("refuses a document from another share, because the AAD does not match", async () => {
    const fake = makeShareFake();
    // The same ciphertext, but the AAD of a different token: this is what a
    // copied-out document looks like, and GCM will not authenticate it.
    fake.opened = openOut({
      documents: openOut().documents.map((document) => ({
        ...document,
        aad: document.aad.replace(VECTOR_TOKEN, "someOtherToken0123456"),
      })),
    });
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), VECTOR_PASSCODE);
    await user.click(screen.getByTestId("share-viewer-submit"));

    expect(
      await screen.findByTestId("share-viewer-refused", undefined, OPEN_WAIT),
    ).toHaveTextContent("did not open");
    expect(screen.queryByTestId("share-payload")).not.toBeInTheDocument();
  });

  it("refuses a truncated key as a broken link, not as a wrong passcode", async () => {
    const fake = makeShareFake();
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET.slice(0, 20)} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), VECTOR_PASSCODE);
    await user.click(screen.getByTestId("share-viewer-submit"));

    const refusal = await screen.findByTestId("share-viewer-refused", undefined, OPEN_WAIT);
    expect(refusal).toHaveTextContent("did not open with this key and passcode");
    expect(refusal).toHaveTextContent(/copied incompletely/);
    expect(screen.queryByTestId("share-payload")).not.toBeInTheDocument();
  });

  it("still offers the PDF when the payload document cannot be read", async () => {
    const fake = makeShareFake();
    fake.opened = openOut({
      documents: openOut().documents.filter((document) => document.name === "pdf"),
    });
    const user = userEvent.setup();
    renderWithProviders(
      <ShareViewer token={VECTOR_TOKEN} urlKey={VECTOR_SECRET} client={fake.client} />,
    );

    await user.type(await screen.findByTestId("share-viewer-passcode"), VECTOR_PASSCODE);
    await user.click(screen.getByTestId("share-viewer-submit"));

    const opened = await screen.findByTestId("share-viewer-open", undefined, OPEN_WAIT);
    expect(within(opened).queryByTestId("share-payload")).not.toBeInTheDocument();
    // The banner is still the first thing on the page.
    expect(within(opened).getByTestId("share-banner-title")).toHaveTextContent(DISCLAIMER_TITLE);
    expect(within(opened).getByTestId("share-download-pdf")).toBeInTheDocument();
  });
});
