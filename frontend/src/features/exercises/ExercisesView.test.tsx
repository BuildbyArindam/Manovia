import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { completion, makeExercisesFake } from "@/test/exerciseFakes";
import { renderWithProviders } from "@/test/utils";

import { ExercisesView } from "./ExercisesView";

async function launch(): Promise<void> {
  fireEvent.click(await screen.findByRole("button", { name: "Try Box breathing" }));
  expect(screen.getByRole("heading", { name: "Box breathing" })).toHaveFocus();
}

describe("exercises page integration", () => {
  it("loads content, explains rule suggestions, and can enter/leave the runner by keyboard", async () => {
    const fake = makeExercisesFake();
    renderWithProviders(<ExercisesView client={fake.client} />);
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "Try Box breathing" });
    await user.selectOptions(screen.getByLabelText("How are you feeling?"), "anxiety");
    await user.selectOptions(screen.getByLabelText("How strong does it feel?"), "high");
    await waitFor(() =>
      expect(screen.getByTestId("exercise-card-box-breathing")).toHaveTextContent("high anxiety"),
    );
    const why = within(screen.getByTestId("exercise-card-box-breathing")).getByText("Why this?");
    await user.click(why);
    expect(screen.getByText(/You selected high anxiety/)).toBeVisible();
    const launchButton = screen.getByRole("button", { name: "Try Box breathing" });
    launchButton.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("heading", { name: "Box breathing" })).toHaveFocus();
    screen.getByRole("button", { name: "Back to exercises" }).focus();
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Try Box breathing" })).toHaveFocus(),
    );
  });

  it("saves a timed completion and invalidates the history query", async () => {
    const fake = makeExercisesFake();
    renderWithProviders(<ExercisesView client={fake.client} />);
    await launch();
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole("radio", { name: "Low" }));
      fireEvent.click(screen.getByRole("button", { name: "Start exercise" }));
      act(() => {
        vi.advanceTimersByTime(12000);
      });
      fireEvent.click(screen.getByRole("radio", { name: "Good" }));
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Save completion" }));
      });
      // TanStack batches observer notifications with setTimeout(0). Do not
      // discard them by restoring real timers while a refetch is in flight.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
    } finally {
      vi.useRealTimers();
    }
    expect(fake.sent).toHaveLength(1);
    await waitFor(() =>
      expect(screen.getByTestId("exercise-history-entry")).toHaveTextContent("2 points higher"),
    );
    expect(screen.getByTestId("exercise-history-entry")).toHaveTextContent("not proof");
    expect(screen.getByTestId("exercise-history-entry")).toHaveTextContent("Before: 2 of 5");
  });

  it("renders lower, unchanged, and missing ratings as observations rather than failed exercises", async () => {
    const fake = makeExercisesFake({
      history: [
        completion({
          id: "lower",
          pre_rating: 4,
          post_rating: 2,
          change: -2,
          summary:
            "You reported feeling 2 points lower afterwards. This is an observation, not proof that the exercise caused a change.",
        }),
        completion({
          id: "same",
          pre_rating: 3,
          post_rating: 3,
          change: 0,
          summary:
            "You reported the same rating before and after. This is an observation, not proof that the exercise caused a change.",
        }),
        completion({
          id: "missing",
          pre_rating: null,
          post_rating: null,
          change: null,
          summary: "Completed. Both optional ratings are needed for a before-and-after comparison.",
        }),
      ],
    });
    renderWithProviders(<ExercisesView client={fake.client} />);
    const entries = await screen.findAllByTestId("exercise-history-entry");
    expect(entries).toHaveLength(3);
    expect(entries[0]).toHaveTextContent("2 points lower");
    expect(entries[1]).toHaveTextContent("same rating");
    expect(entries[2]).toHaveTextContent("Before: not rated");
    expect(screen.getByTestId("exercise-history")).not.toHaveTextContent(
      /failed exercise|improved by|cured/i,
    );
  });

  it("paginates history without losing the loaded catalogue", async () => {
    const fake = makeExercisesFake({
      history: Array.from({ length: 21 }, (_, id) => completion({ id: `item-${id}` })),
    });
    renderWithProviders(<ExercisesView client={fake.client} />);
    await waitFor(() => expect(screen.getAllByTestId("exercise-history-entry")).toHaveLength(20));
    fireEvent.click(screen.getByRole("button", { name: "Older completions" }));
    await waitFor(() => expect(screen.getAllByTestId("exercise-history-entry")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Newer completions" }));
    await waitFor(() => expect(screen.getAllByTestId("exercise-history-entry")).toHaveLength(20));
    expect(fake.calls.filter((call) => call.path === "/api/v1/exercises")).toHaveLength(1);
  });

  it("shows honest content/history failures with explicit retries", async () => {
    const fake = makeExercisesFake();
    fake.failCatalog = new Error("fixture: unavailable");
    fake.failHistory = new Error("fixture: unavailable");
    renderWithProviders(<ExercisesView client={fake.client} />);
    await screen.findByText(/Exercises could not be loaded/);
    await screen.findByText(/History could not be loaded/);
    fake.failCatalog = null;
    fake.failHistory = null;
    fireEvent.click(screen.getByRole("button", { name: "Retry exercises" }));
    fireEvent.click(screen.getByRole("button", { name: "Retry history" }));
    await screen.findByRole("button", { name: "Try Box breathing" });
    await screen.findByText(/No saved completions yet/);
  });

  it("has no axe violations on the loaded catalogue or runner", async () => {
    const fake = makeExercisesFake();
    const { container } = renderWithProviders(<ExercisesView client={fake.client} />);
    await screen.findByRole("button", { name: "Try Box breathing" });
    expect(await axe(container)).toHaveNoViolations();
    await launch();
    expect(await axe(container)).toHaveNoViolations();
  });
});
