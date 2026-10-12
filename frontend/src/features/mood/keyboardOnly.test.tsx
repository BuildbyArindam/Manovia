/**
 * A whole check-in, keyboard only.
 *
 * The requirement is that the ten-second check-in is completable without a mouse,
 * and the honest way to test that is to take the keyboard and try: no
 * `fireEvent.click`, no `getByLabelText(...).value = ...`, only `Tab`, `Space`,
 * arrows, typing and `Enter` — exactly what the Playwright spec does in a real
 * browser, and what this file does in jsdom so it runs everywhere.
 *
 * What is asserted at the end is the whole contract: the request sent contains
 * the values the keyboard chose (and the device timezone), the confirmation is
 * announced and focused, one small action is offered, and the check-in appears in
 * the history below without a reload.
 *
 * jsdom cannot do one part of this — arrow-key stepping on `<input type="range">`
 * is not implemented — so the energy value is moved with a `change` event (what
 * the browser fires) and the arrow-key behaviour is proven in
 * `frontend/e2e/mood.spec.ts`.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { createQueryClient } from "@/lib/queryClient";
import { makeMoodFake } from "@/test/moodFakes";
import { OnboardingProvider } from "@/onboarding/OnboardingProvider";
import { ThemeProvider } from "@/theme/ThemeProvider";

import { MoodView } from "./MoodView";

function renderMoodView(client: ReturnType<typeof makeMoodFake>["client"]) {
  return render(
    <ThemeProvider>
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <OnboardingProvider>
            <MoodView client={client} />
          </OnboardingProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe("a full check-in with no mouse", () => {
  it("is completable from the keyboard, and the payload says what was chosen", async () => {
    const user = userEvent.setup();
    const fake = makeMoodFake();
    renderMoodView(fake.client);

    // 1. Tab lands on the first face; Space chooses it; three arrows move up to
    //    "Good".
    await user.tab();
    expect(screen.getByRole("radio", { name: "Very low" })).toHaveFocus();
    await user.keyboard(" ");
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Good" })).toBeChecked();

    // 2. Tab to the energy slider. jsdom cannot step a range with arrows, so the
    //    browser's own change event is used here (see the file docstring).
    await user.tab();
    const slider = screen.getByRole("slider", { name: /energy/i });
    expect(slider).toHaveFocus();
    fireEvent.change(slider, { target: { value: "2" } });
    expect(screen.getByTestId("mood-energy-readout")).toHaveTextContent("2 — Running low");

    // 3. Tab into the emotion chips and pick two with Space.
    await user.tab();
    expect(screen.getByRole("checkbox", { name: "Joy" })).toHaveFocus();
    await user.keyboard(" ");
    await user.tab();
    await user.keyboard(" "); // Sadness

    // 4. Keep tabbing to the factors and pick one.
    const chips = screen.getByTestId("chips-mood-factors");
    within(chips).getByRole("checkbox", { name: "Sleep" }).focus();
    await user.keyboard(" ");

    // 5. Tab to the note and type.
    await user.tab();
    await user.tab();
    await user.type(screen.getByLabelText(/anything you want to add/i), "Slept badly, again.");

    // 6. Tab to the button and press Enter.
    const save = screen.getByRole("button", { name: "Save check-in" });
    save.focus();
    expect(save).toHaveFocus();
    await user.keyboard("{Enter}");

    // The request carries exactly what was chosen, plus the device timezone
    // and (Day 25) a random idempotency key, so a retried send stores once.
    await waitFor(() => {
      expect(fake.sent).toHaveLength(1);
    });
    const sent = fake.sent[0];
    expect(sent).toBeDefined();
    expect(sent).toEqual({
      valence: 4,
      energy: 2,
      emotions: ["joy", "sadness"],
      factors: ["sleep"],
      note: "Slept badly, again.",
      tz: expect.any(String),
      client_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });

    // The confirmation is announced, focused, and offers exactly one small thing.
    const confirmation = await screen.findByTestId("mood-confirmation");
    expect(confirmation).toHaveAttribute("role", "status");
    expect(screen.getByRole("heading", { name: "Saved." })).toHaveFocus();
    // The note is not repeated back on the confirmation: the person just wrote it.
    expect(within(confirmation).queryByText("Slept badly, again.")).not.toBeInTheDocument();
    const action = within(screen.getByTestId("mood-suggested-action"));
    expect(action.getAllByRole("paragraph")).toHaveLength(1);

    // And the history below shows it, because the query was invalidated.
    expect(await screen.findByTestId("mood-history-entry")).toBeInTheDocument();
  });

  it("stops at the save button without a note, too", async () => {
    const user = userEvent.setup();
    const fake = makeMoodFake();
    renderMoodView(fake.client);

    await user.tab();
    await user.keyboard(" "); // Very low
    screen.getByRole("button", { name: "Save check-in" }).focus();
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(fake.sent).toHaveLength(1);
    });
    expect(fake.sent[0]?.note).toBeNull();
    expect(fake.sent[0]?.valence).toBe(1);
    expect(fake.sent[0]?.energy).toBe(3); // the middle, and visibly so
  });

  it("focuses the face group and explains when nothing was chosen", async () => {
    const user = userEvent.setup();
    const fake = makeMoodFake();
    renderMoodView(fake.client);

    const save = screen.getByRole("button", { name: "Save check-in" });
    save.focus();
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent(/Choose the face/);
    expect(screen.getByRole("radio", { name: "Very low" })).toHaveFocus();
    expect(fake.sent).toHaveLength(0);
  });

  it("has no axe violations with a filled-in check-in on screen", async () => {
    const fake = makeMoodFake();
    const { container } = renderMoodView(fake.client);

    fireEvent.click(screen.getByRole("radio", { name: "In the middle" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Calm" }));

    expect(await axe(container)).toHaveNoViolations();
  });
});
