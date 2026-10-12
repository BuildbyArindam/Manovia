/**
 * Day 25 regression: the dialog is portalled to <body>.
 *
 * Rendered in place, a dialog opened from the sticky header lived inside the
 * header's stacking context, and the mobile bottom navigation was painted over
 * the lower part of "Need help now?". The browser check is in
 * e2e/day25/a11y.spec.ts; this pins the structural cause.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Modal } from "./Modal";

describe("Modal placement", () => {
  it("renders into <body>, outside whatever stacking context opened it", () => {
    const { container } = render(
      <header style={{ position: "sticky", zIndex: 30 }} data-testid="opener">
        <Modal labelledBy="t" onClose={() => undefined} testId="the-modal">
          <h2 id="t">Title</h2>
        </Modal>
      </header>,
    );
    const overlay = screen.getByTestId("the-modal");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByTestId("opener").contains(overlay)).toBe(false);
    expect(container.contains(overlay)).toBe(false);
    expect(overlay.parentElement).toBe(document.body);
  });
});
