import { act, fireEvent, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { afterEach, describe, expect, it, vi } from "vitest";

import { breathingExercise, makeExercisesFake, plannerExercise } from "@/test/exerciseFakes";
import { stubMatchMedia } from "@/test/matchMedia";
import { renderWithProviders } from "@/test/utils";

import { ExerciseRunner } from "./ExerciseRunner";

function advance(ms: number): void {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

function start(): void {
  fireEvent.click(screen.getByRole("button", { name: "Start exercise" }));
}

afterEach(() => {
  vi.useRealTimers();
});

describe("exercise runner timing with fake timers", () => {
  it("uses exact step timings, pauses fractional active time, resumes, and finishes only at the end", () => {
    vi.useFakeTimers();
    const intervalSpy = vi.spyOn(window, "setInterval");
    const clearSpy = vi.spyOn(window, "clearInterval");
    renderWithProviders(<ExerciseRunner exercise={breathingExercise()} onClose={vi.fn()} />);
    start();
    expect(screen.getByTestId("step-countdown")).toHaveTextContent("4");
    const firstAnnouncement = screen.getByTestId("step-announcement").textContent;
    advance(1250);
    expect(screen.getByTestId("step-countdown")).toHaveTextContent("3");
    expect(screen.getByTestId("step-announcement").textContent).toBe(firstAnnouncement);
    advance(125); // between interval ticks: the pause accounts for this fraction
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(screen.getByRole("button", { name: "Resume" })).toBeVisible();
    const svg = screen.getByTestId("animated-breathing-guide").querySelector("svg");
    expect(svg).toHaveStyle({ animationPlayState: "paused" });
    advance(30000);
    expect(screen.getByTestId("step-countdown")).toHaveTextContent("3");
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    advance(2500);
    expect(screen.getByTestId("exercise-instruction")).toHaveTextContent("Breathe in gently");
    advance(125);
    // The tick granularity is 250ms; account for the remaining fraction on pause.
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(screen.getByTestId("exercise-instruction")).toHaveTextContent(
      "Pause only if comfortable",
    );
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    advance(4000);
    expect(screen.getByTestId("exercise-instruction")).toHaveTextContent("Breathe out gently");
    advance(3750);
    expect(screen.queryByTestId("exercise-finished")).not.toBeInTheDocument();
    advance(250);
    expect(screen.getByTestId("exercise-finished")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Exercise finished" })).toHaveFocus();
    // jsdom focus queues selectionchange with setTimeout(0); flush it, not
    // the exercise interval, before asserting all timers have been cleaned up.
    advance(1);
    expect(clearSpy).toHaveBeenCalledTimes(3);
    for (const result of intervalSpy.mock.results) {
      if (result.type === "return") {
        expect(clearSpy).toHaveBeenCalledWith(result.value);
      }
    }
    expect(vi.getTimerCount()).toBe(0);
    advance(60000);
    expect(screen.getByTestId("exercise-finished")).toBeVisible();
  });

  it("pauses a hidden page and never resumes or skips instructions automatically", () => {
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    renderWithProviders(<ExerciseRunner exercise={breathingExercise()} onClose={vi.fn()} />);
    start();
    advance(1000);
    visibility.mockReturnValue("hidden");
    fireEvent(document, new Event("visibilitychange"));
    advance(60000);
    expect(screen.getByRole("button", { name: "Resume" })).toBeVisible();
    expect(screen.getByTestId("step-countdown")).toHaveTextContent("3");
    visibility.mockReturnValue("visible");
    fireEvent(document, new Event("visibilitychange"));
    expect(screen.getByRole("button", { name: "Resume" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    advance(3000);
    expect(screen.getByTestId("exercise-instruction")).toHaveTextContent(
      "Pause only if comfortable",
    );
  });

  it("pauses after a suspended timer or clock jump instead of silently skipping holds", () => {
    vi.useFakeTimers();
    renderWithProviders(<ExerciseRunner exercise={breathingExercise()} onClose={vi.fn()} />);
    start();
    advance(1000);
    vi.setSystemTime(Date.now() + 10000);
    advance(250);
    expect(screen.getByRole("button", { name: "Resume" })).toBeVisible();
    expect(screen.getByTestId("step-countdown")).toHaveTextContent("3");
    expect(screen.getByTestId("exercise-instruction")).toHaveTextContent("Breathe in gently");
  });

  it("stopping early records nothing and unmount clears the interval", () => {
    vi.useFakeTimers();
    const close = vi.fn();
    const fake = makeExercisesFake();
    const { unmount } = renderWithProviders(
      <ExerciseRunner exercise={breathingExercise()} client={fake.client} onClose={close} />,
    );
    start();
    advance(500);
    fireEvent.click(screen.getByRole("button", { name: "Stop exercise" }));
    expect(close).toHaveBeenCalledOnce();
    expect(fake.sent).toEqual([]);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("motion and announcements", () => {
  it("uses a static countdown with visible text under prefers-reduced-motion", () => {
    stubMatchMedia(true);
    renderWithProviders(<ExerciseRunner exercise={breathingExercise()} onClose={vi.fn()} />);
    start();
    expect(screen.getByTestId("breathing-guide")).toHaveAttribute(
      "data-motion",
      "static-countdown",
    );
    expect(screen.getByTestId("static-countdown")).toBeVisible();
    expect(screen.queryByTestId("animated-breathing-guide")).not.toBeInTheDocument();
    expect(screen.getByTestId("exercise-instruction")).toBeVisible();
    expect(screen.getByTestId("exercise-instruction")).toHaveTextContent("Breathe in gently");
    expect(screen.getByTestId("step-announcement")).toHaveTextContent(
      "Step 1 of 3. Breathe in gently",
    );
    expect(screen.getByRole("timer")).toHaveAttribute("aria-live", "off");
    expect(screen.getByLabelText("Use a static countdown (no animation)")).toBeDisabled();
  });

  it("allows text-only without an OS preference and responds to a live preference change", () => {
    const media = stubMatchMedia(false);
    renderWithProviders(<ExerciseRunner exercise={breathingExercise()} onClose={vi.fn()} />);
    start();
    expect(screen.getByTestId("animated-breathing-guide")).toBeVisible();
    fireEvent.click(screen.getByLabelText("Use a static countdown (no animation)"));
    expect(screen.getByTestId("static-countdown")).toBeVisible();
    fireEvent.click(screen.getByLabelText("Use a static countdown (no animation)"));
    act(() => media.emit(true));
    expect(screen.getByTestId("static-countdown")).toBeVisible();
  });

  it("has no axe violations before starting or while running", async () => {
    const { container } = renderWithProviders(
      <ExerciseRunner exercise={breathingExercise()} onClose={vi.fn()} />,
    );
    expect(await axe(container)).toHaveNoViolations();
    start();
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("haptics and private completion", () => {
  it("keeps vibration off by default, opts in at step boundaries, and cancels on pause", () => {
    vi.useFakeTimers();
    const vibrate = vi.fn((pattern: number | number[]) => Boolean(pattern));
    Object.defineProperty(navigator, "vibrate", { configurable: true, value: vibrate });
    const { unmount } = renderWithProviders(
      <ExerciseRunner exercise={breathingExercise()} onClose={vi.fn()} />,
    );
    start();
    expect(vibrate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("Use a brief vibration at each step (optional)"));
    expect(vibrate).toHaveBeenCalledWith(30);
    advance(4000);
    expect(vibrate.mock.calls.filter(([duration]) => duration === 30)).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(vibrate).toHaveBeenLastCalledWith(0);
    unmount();
    Reflect.deleteProperty(navigator, "vibrate");
  });

  it("does not interrupt the runner if a browser exposes but blocks vibration", () => {
    const vibrate = vi.fn(() => {
      throw new Error("fixture: permission blocked");
    });
    Object.defineProperty(navigator, "vibrate", { configurable: true, value: vibrate });
    const { unmount } = renderWithProviders(
      <ExerciseRunner exercise={breathingExercise()} onClose={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText("Use a brief vibration at each step (optional)"));
    start();
    expect(screen.getByTestId("exercise-instruction")).toBeVisible();
    unmount();
    Reflect.deleteProperty(navigator, "vibrate");
  });

  it("saves optional pre/post ratings and never sends a planner choice or personal text", async () => {
    vi.useFakeTimers();
    const exercise = plannerExercise();
    const fake = makeExercisesFake({ exercises: [exercise] });
    const saved = vi.fn();
    renderWithProviders(
      <ExerciseRunner exercise={exercise} client={fake.client} onClose={vi.fn()} onSaved={saved} />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Low" }));
    start();
    fireEvent.click(screen.getByRole("radio", { name: "Fill a glass of water" }));
    expect(screen.getByTestId("selected-action")).toHaveTextContent("Fill a glass of water");
    advance(4000);
    fireEvent.click(screen.getByRole("radio", { name: "Good" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save completion" }));
    });
    expect(fake.sent).toHaveLength(1);
    expect(fake.sent[0]).toEqual({
      completion_id: expect.any(String),
      pre_rating: 2,
      post_rating: 4,
    });
    expect(Object.keys(fake.sent[0] ?? {})).toEqual(["completion_id", "pre_rating", "post_rating"]);
    expect(saved).toHaveBeenCalledOnce();
    expect(screen.getByTestId("completion-saved")).toHaveTextContent("not proof");
    expect(JSON.stringify(localStorage)).not.toContain("Fill a glass of water");
  });

  it("runs offline once loaded and retries a failed save with the exact same UUID and ratings", async () => {
    vi.useFakeTimers();
    const fake = makeExercisesFake();
    fake.offline = true;
    renderWithProviders(
      <ExerciseRunner exercise={breathingExercise()} client={fake.client} onClose={vi.fn()} />,
    );
    start();
    advance(12000);
    expect(fake.calls).toHaveLength(0); // no network is needed by the timer
    fireEvent.click(screen.getByRole("radio", { name: "Good" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save completion" }));
    });
    expect(screen.getByTestId("completion-unsaved")).toHaveTextContent("Not saved");
    expect(screen.getByRole("radio", { name: "Good" })).toBeDisabled();
    expect(screen.queryByTestId("completion-saved")).not.toBeInTheDocument();
    expect(fake.history).toHaveLength(0);
    fake.offline = false;
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry saving" }));
    });
    expect(fake.sent).toHaveLength(2);
    expect(fake.sent[0]).toEqual(fake.sent[1]);
    expect(fake.history).toHaveLength(1);
    expect(screen.getByTestId("completion-saved")).toBeVisible();
    expect(JSON.stringify(localStorage)).not.toContain("post_rating");
  });

  it("supports finishing without ratings or without saving", async () => {
    vi.useFakeTimers();
    const fake = makeExercisesFake();
    const close = vi.fn();
    renderWithProviders(
      <ExerciseRunner exercise={breathingExercise()} client={fake.client} onClose={close} />,
    );
    start();
    advance(12000);
    fireEvent.click(screen.getByRole("button", { name: "Finish without saving" }));
    expect(close).toHaveBeenCalledOnce();
    expect(fake.sent).toHaveLength(0);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save completion" }));
    });
    expect(fake.sent[0]).toMatchObject({ pre_rating: null, post_rating: null });
  });
});
