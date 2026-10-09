/**
 * Modal behaviour: the focus contract every dialog in the app inherits.
 *
 * Tab/Shift+Tab are dispatched directly rather than through `user-event`
 * because user-event implements its own tab order and would ignore the trap's
 * `preventDefault` — which is precisely the thing under test.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ReactElement } from "react";

import { Modal } from "@/components/Modal";

function Harness({ onClose }: { onClose: () => void }): ReactElement {
  return (
    <Modal labelledBy="dialog-title" describedBy="dialog-description" onClose={onClose}>
      <h2 id="dialog-title">Dialog title</h2>
      <p id="dialog-description">Dialog description</p>
      <button type="button">First</button>
      <button type="button">Last</button>
    </Modal>
  );
}

function OpenableHarness(): ReactElement {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open dialog
      </button>
      {open ? (
        <Modal labelledBy="dialog-title" onClose={() => setOpen(false)}>
          <h2 id="dialog-title">Dialog title</h2>
          <button type="button">Inside</button>
        </Modal>
      ) : null}
    </>
  );
}

describe("Modal", () => {
  it("exposes a labelled, described modal dialog", () => {
    render(<Harness onClose={() => undefined} />);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("Dialog title");
    expect(dialog).toHaveAccessibleDescription("Dialog description");
  });

  it("moves focus into the dialog when it opens", () => {
    render(<OpenableHarness />);

    fireEvent.click(screen.getByRole("button", { name: "Open dialog" }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveFocus();
  });

  it("returns focus to the control that opened it", async () => {
    // user-event, not fireEvent: a real click focuses the button first, and that
    // is the element focus must come back to.
    const user = userEvent.setup();
    render(<OpenableHarness />);

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    expect(screen.getByRole("dialog")).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open dialog" })).toHaveFocus();
  });

  it("traps Tab inside the dialog", () => {
    render(<Harness onClose={() => undefined} />);

    const dialog = screen.getByRole("dialog");
    const [first, last] = Array.from(dialog.querySelectorAll("button"));
    if (first === undefined || last === undefined) {
      throw new Error("fixture missing buttons");
    }

    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(first).toHaveFocus();

    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
  });

  it("keeps focus on the dialog itself when there is nothing focusable inside", () => {
    render(
      <Modal labelledBy="empty-title" onClose={() => undefined}>
        <h2 id="empty-title">Nothing to focus</h2>
      </Modal>,
    );

    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Tab" });

    expect(dialog).toHaveFocus();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on a backdrop click but not on a click inside", () => {
    const onClose = vi.fn();
    render(
      <Modal labelledBy="dialog-title" onClose={onClose} testId="overlay">
        <h2 id="dialog-title">Dialog title</h2>
        <button type="button">Inside</button>
      </Modal>,
    );

    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.mouseDown(screen.getByTestId("overlay"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
