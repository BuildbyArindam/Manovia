/**
 * CrisisCard: the visual half of the deterministic crisis reply.
 *
 * What matters here is usability under stress, so the tests assert the things a
 * person in crisis depends on: the emergency number is first and diallable,
 * `tel:` and `sms:` links are real anchors with real hrefs, every control is a
 * large tap target, a keyboard user can reach and focus each of them in order,
 * and the pre-written message is rendered verbatim — the component never
 * rewrites the copy it is given.
 */

import { axe } from "jest-axe";
import userEvent from "@testing-library/user-event";
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CrisisCard } from "@/components/CrisisCard";
import { crisisMessage, crisisResource, TEST_RESOURCES } from "@/test/crisisFixtures";
import { renderWithProviders } from "@/test/utils";

describe("CrisisCard", () => {
  it("renders every helpline as a diallable tel: link", () => {
    renderWithProviders(<CrisisCard resources={TEST_RESOURCES} />);

    expect(screen.getByRole("link", { name: "Call 5550100" })).toHaveAttribute(
      "href",
      "tel:5550100",
    );
    expect(screen.getByRole("link", { name: "Call 911" })).toHaveAttribute("href", "tel:911");
  });

  it("renders a text line as an sms: link that carries the keyword", () => {
    renderWithProviders(<CrisisCard resources={TEST_RESOURCES} />);

    const text = screen.getByRole("link", { name: /Text 55501/ });
    expect(text).toHaveAttribute("href", "sms:55501");
    expect(text).toHaveTextContent("HOME");
    expect(screen.getByText("Text HOME to 55501.")).toBeInTheDocument();
  });

  it("never renders a tel: link for an entry that has no number", () => {
    renderWithProviders(<CrisisCard resources={TEST_RESOURCES} />);

    const directory = screen.getByTestId("crisis-resource-test-directory");
    expect(within(directory).queryByRole("link", { name: /^Call/ })).not.toBeInTheDocument();
    expect(within(directory).getByRole("link", { name: "Visit website" })).toHaveAttribute(
      "href",
      "https://example.test/directory",
    );
  });

  it("puts the emergency number first and above the rest of the list", () => {
    renderWithProviders(<CrisisCard resources={TEST_RESOURCES} emergency={TEST_RESOURCES[0]} />);

    const card = screen.getByTestId("crisis-card");
    const emergency = screen.getByTestId("crisis-emergency");
    expect(emergency).toHaveTextContent("Emergency services");
    // The emergency block precedes the list in DOM order, so it is also what a
    // screen reader and a scrolling thumb meet first.
    expect(card.compareDocumentPosition(emergency) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("crisis-resource-test-call-line")).toBeInTheDocument();
    // The emergency entry is not rendered twice.
    expect(screen.queryByTestId("crisis-resource-test-emergency")).not.toBeInTheDocument();
  });

  it("gives every action a tap target at least 48px tall", () => {
    renderWithProviders(<CrisisCard resources={TEST_RESOURCES} emergency={TEST_RESOURCES[0]} />);

    const links = screen.getAllByRole("link");
    expect(links.length).toBeGreaterThanOrEqual(4);
    for (const link of links) {
      expect(link.className).toContain("min-h-12");
      expect(link.className).toContain("px-5");
    }
  });

  it("is reachable and focusable by keyboard, in order", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CrisisCard resources={TEST_RESOURCES} emergency={TEST_RESOURCES[0]} />);

    const links = screen.getAllByRole("link");
    for (const link of links) {
      await user.tab();
      expect(link).toHaveFocus();
    }
  });

  it("renders the pre-written message verbatim", () => {
    const message = crisisMessage();

    renderWithProviders(<CrisisCard message={message} resources={TEST_RESOURCES} />);

    expect(screen.getByRole("heading", { level: 2, name: message.title })).toBeInTheDocument();
    for (const paragraph of message.body) {
      expect(screen.getByText(paragraph)).toBeInTheDocument();
    }
    for (const step of message.safety_steps) {
      expect(screen.getByText(step)).toBeInTheDocument();
    }
    expect(screen.getByTestId("crisis-emergency-instruction")).toHaveTextContent(
      message.emergency_instruction ?? "",
    );
    expect(screen.getByText(message.closing ?? "")).toBeInTheDocument();
    expect(screen.getByText(message.helpline_intro ?? "")).toBeInTheDocument();
  });

  it("announces itself and exposes the disclaimer and verification date", () => {
    renderWithProviders(
      <CrisisCard
        message={crisisMessage()}
        resources={TEST_RESOURCES}
        lastVerified="2026-10-09"
        disclaimer="Synthetic test data, not a real helpline."
      />,
    );

    const card = screen.getByTestId("crisis-card");
    expect(card).toHaveAttribute("role", "status");
    expect(card).toHaveAttribute("aria-live", "polite");
    expect(card).toHaveTextContent("Last checked by a person on 2026-10-09.");
    expect(card).toHaveTextContent("Synthetic test data, not a real helpline.");
  });

  it("can drop its own heading when the surface already has one", () => {
    renderWithProviders(<CrisisCard heading={null} resources={TEST_RESOURCES} />);

    expect(screen.queryByRole("heading", { level: 2 })).not.toBeInTheDocument();
    // Still accessibly named: a card with no heading must not become an
    // unlabelled landmark for a screen reader user.
    const card = screen.getByTestId("crisis-card");
    expect(card).toHaveAttribute("aria-label", "Helplines");
    expect(card).not.toHaveAttribute("aria-labelledby");
  });

  it("labels itself by its heading when it has one", () => {
    renderWithProviders(<CrisisCard resources={TEST_RESOURCES} />);

    const card = screen.getByTestId("crisis-card");
    const heading = screen.getByRole("heading", { level: 2, name: "Helplines" });
    expect(card).toHaveAttribute("aria-labelledby", heading.id);
    expect(card).not.toHaveAttribute("aria-label");
  });

  it("says when an entry could not be fully confirmed", () => {
    const flagged = crisisResource({
      id: "test-flagged",
      name: "Partly Confirmed Line",
      needs_verification: true,
      verification_note: "Hours conflict between two listings.",
    });

    renderWithProviders(<CrisisCard resources={[flagged]} />);

    expect(screen.getByText(/We could not fully confirm this entry/)).toBeInTheDocument();
  });

  it("renders nothing when it has nothing to show", () => {
    const { container } = renderWithProviders(<CrisisCard resources={[]} />);

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("crisis-card")).not.toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = renderWithProviders(
      <CrisisCard
        message={crisisMessage()}
        resources={TEST_RESOURCES}
        emergency={TEST_RESOURCES[0]}
        lastVerified="2026-10-09"
        disclaimer="Synthetic test data."
      />,
    );

    expect(await axe(container)).toHaveNoViolations();
  });
});
