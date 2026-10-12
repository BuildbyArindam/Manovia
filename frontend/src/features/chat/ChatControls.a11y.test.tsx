/**
 * Day 25 verification: the chat page shows two selects labelled "Language" —
 * the header's (interface language) and this one (the language of the
 * pre-written crisis and check-in messages). The keyboard walkthrough heard
 * them as two identical controls; this one now carries its explanation as its
 * accessible description, so the two can be told apart without sight.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatControls } from "./ChatControls";

describe("ChatControls language select", () => {
  it("is described by the sentence that says what it changes", () => {
    render(
      <ChatControls
        saveHistory={false}
        sessionIsPersistent={false}
        hasSession={false}
        locale="en"
        onSaveHistoryChange={vi.fn()}
        onClear={vi.fn()}
        onNewChat={vi.fn()}
        onLocaleChange={vi.fn()}
      />,
    );
    const select = screen.getByTestId("language-select");
    expect(select).toHaveAccessibleName("Language");
    expect(select).toHaveAccessibleDescription(/changes the written crisis and check-in messages/);
  });
});
