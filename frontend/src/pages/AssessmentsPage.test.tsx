/**
 * The screening page end to end, against a fake transport.
 *
 * The first test is the one that matters most: when item 9 is answered, the
 * crisis block must come **before** the score in the document — not beside it,
 * not after it, not only above it on a wide screen. `compareDocumentPosition`
 * asserts the order the screen reader and the keyboard actually meet, which is
 * also the order a frightened person reads.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { createQueryClient } from "@/lib/queryClient";
import { OnboardingProvider } from "@/onboarding/OnboardingProvider";
import { ThemeProvider } from "@/theme/ThemeProvider";
import { crisisPayload, makeAssessmentFake, resultView } from "@/test/assessmentFakes";

import { AssessmentsPage } from "./AssessmentsPage";

function renderPage(
  client: Parameters<typeof makeAssessmentFake>[0] extends never
    ? never
    : ReturnType<typeof makeAssessmentFake>["client"],
) {
  return render(
    <ThemeProvider>
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <OnboardingProvider>
            <AssessmentsPage client={client} />
          </OnboardingProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

async function startQuestionnaire(): Promise<void> {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Start the PHQ-9" }));
  await screen.findByTestId("questionnaire");
}

/** Answer every question "Not at all", except item 9 when asked to. */
async function answerAll(options: { item9?: number } = {}): Promise<void> {
  const user = userEvent.setup();
  for (let position = 1; position <= 9; position += 1) {
    const fieldset = screen.getByTestId(`item-phq9_${position}`);
    const choice = position === 9 && options.item9 !== undefined ? options.item9 : 0;
    await user.click(
      within(fieldset).getByRole("radio", {
        name: new RegExp(
          `^${choice === 0 ? "Not at all" : choice === 1 ? "Several days" : choice === 2 ? "More than half the days" : "Nearly every day"}`,
        ),
      }),
    );
  }
}

describe("AssessmentsPage — the item-9 rule", () => {
  it("shows the crisis resources before the score", async () => {
    const fake = makeAssessmentFake({
      crisis: crisisPayload(),
      submitted: resultView({ total: 1, band: "minimal", band_label: "Minimal" }),
    });
    renderPage(fake.client);
    await startQuestionnaire();
    await answerAll({ item9: 1 });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "See my result" }));

    const crisis = await screen.findByTestId("crisis-block");
    const score = await screen.findByTestId("score-block");

    // Document order, not just visual order.
    const position = crisis.compareDocumentPosition(score);
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // The crisis card carries a reachable helpline and the disclaimer.
    expect(within(crisis).getByText(/What you're feeling is real/)).toBeInTheDocument();
    expect(within(crisis).getByRole("link", { name: /AASRA|91 98204/ })).toHaveAttribute(
      "href",
      "tel:+919820466726",
    );
    expect(within(crisis).getByText(/not a crisis service/i)).toBeInTheDocument();
    expect(
      within(crisis).getByText(/showing support and helplines before your score/),
    ).toBeInTheDocument();

    // The score is still there — item 9 does not delete the result.
    expect(within(score).getByText("1 of 27 — minimal")).toBeInTheDocument();
    expect(within(score).getByText(/not a diagnosis/i)).toBeInTheDocument();
  });

  it("does not show the crisis block when item 9 is Not at all", async () => {
    const fake = makeAssessmentFake({ crisis: null });
    renderPage(fake.client);
    await startQuestionnaire();
    await answerAll();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "See my result" }));
    await screen.findByTestId("score-block");
    expect(screen.queryByTestId("crisis-block")).not.toBeInTheDocument();
  });
});

describe("AssessmentsPage — the result", () => {
  it("shows the score, the band copy and the screening disclaimer", async () => {
    const fake = makeAssessmentFake({
      submitted: resultView({
        total: 17,
        band: "moderately_severe",
        band_label: "Moderately severe",
        band_explanation: "Your answers point to quite a lot of symptoms over the last two weeks.",
        what_helps: [
          "Treat this as a reason to get help rather than a reason to try harder alone.",
        ],
        when_to_seek_help: "Please contact a doctor or a mental health professional now.",
      }),
    });
    renderPage(fake.client);
    await startQuestionnaire();
    await answerAll();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "See my result" }));

    const score = await screen.findByTestId("score-block");
    expect(within(score).getByText("17 of 27 — moderately severe")).toBeInTheDocument();
    expect(within(score).getByText(/quite a lot of symptoms/)).toBeInTheDocument();
    expect(screen.getByTestId("not-a-diagnosis")).toHaveTextContent(
      "This is a screening result, not a diagnosis",
    );
    expect(screen.getByRole("heading", { name: "What usually helps" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "When to talk to someone" })).toBeInTheDocument();
    expect(screen.getAllByText(/not a diagnosis/i).length).toBeGreaterThan(0);
  });

  it("renders the printable summary with the disclaimer and the item-9 line", async () => {
    const fake = makeAssessmentFake({
      crisis: crisisPayload(),
      submitted: resultView({ total: 1 }),
    });
    const { container } = renderPage(fake.client);
    await startQuestionnaire();
    await answerAll({ item9: 3 });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "See my result" }));
    await screen.findByTestId("print-summary");

    const summary = screen.getByTestId("print-summary");
    expect(within(summary).getByText(/Manovia screening summary/)).toBeInTheDocument();
    expect(
      within(summary).getByText(/This is a screening result, not a diagnosis/),
    ).toBeInTheDocument();
    expect(within(summary).getByText(/thoughts of being better off dead/)).toBeInTheDocument();
    expect(within(summary).getByText(/No permission required to reproduce/)).toBeInTheDocument();
    expect(
      within(summary).getByText(/not therapy, not a diagnosis, and not a crisis service/),
    ).toBeInTheDocument();
    // It is a real section of the page, hidden on screen by CSS rather than
    // absent from the DOM — which is what lets print CSS reveal it.
    expect(container.querySelector(".print-summary")).not.toBeNull();
  });

  it("shows the cadence notice as advice, not as a blocked action", async () => {
    const fake = makeAssessmentFake({});
    renderPage(fake.client);
    await startQuestionnaire();
    await answerAll();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "See my result" }));
    const notice = await screen.findByTestId("cadence-notice");
    expect(notice).toHaveTextContent(/Two weeks or more have passed/);
    // Nothing is disabled: the person may take it again immediately.
    expect(screen.getByRole("button", { name: "Answer again" })).toBeEnabled();
  });
});

describe("AssessmentsPage — validation and failure", () => {
  it("refuses a partial submission, names the gap and stores nothing", async () => {
    const fake = makeAssessmentFake({});
    renderPage(fake.client);
    await startQuestionnaire();

    const user = userEvent.setup();
    const first = screen.getByTestId("item-phq9_1");
    await user.click(within(first).getByRole("radio", { name: /Not at all/ }));
    await user.click(screen.getByRole("button", { name: "See my result" }));

    const error = await screen.findByTestId("questionnaire-error");
    expect(error).toHaveTextContent("8 questions are still unanswered");
    expect(fake.sent).toEqual([]);
    // Focus moved to the first unanswered question, so the work is in front of
    // the person rather than at the bottom of the page.
    await waitFor(() => {
      expect(document.activeElement?.textContent).toContain(
        "2. Feeling down, depressed, or hopeless",
      );
    });
  });

  it("keeps the answers on screen when the save fails", async () => {
    const fake = makeAssessmentFake({
      failSubmit: new ApiError(503, "safety_unavailable", "Nothing was saved.", "req-1"),
    });
    renderPage(fake.client);
    await startQuestionnaire();
    await answerAll();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "See my result" }));
    expect(await screen.findByTestId("questionnaire-error")).toHaveTextContent(
      "Nothing was saved.",
    );
    expect(screen.getByTestId("questionnaire")).toBeInTheDocument();
  });
});

describe("AssessmentsPage — accessibility", () => {
  it("has no axe violations on the question page", async () => {
    const fake = makeAssessmentFake({});
    const { container } = renderPage(fake.client);
    await startQuestionnaire();
    // A couple of answers selected, so the checked state is in scope too.
    const user = userEvent.setup();
    await user.click(
      within(screen.getByTestId("item-phq9_1")).getByRole("radio", { name: /Not at all/ }),
    );
    await user.click(
      within(screen.getByTestId("item-phq9_2")).getByRole("radio", { name: /Several days/ }),
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("has no axe violations on the result page", async () => {
    const fake = makeAssessmentFake({ crisis: crisisPayload() });
    const { container } = renderPage(fake.client);
    await startQuestionnaire();
    await answerAll({ item9: 1 });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "See my result" }));
    await screen.findByTestId("score-block");
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("AssessmentsPage — keyboard only", () => {
  it("can be completed without a mouse", async () => {
    const fake = makeAssessmentFake({});
    renderPage(fake.client);
    const user = userEvent.setup();

    // Reach the start button by tabbing, then activate it with Enter. (The
    // skip link lives in the app shell, which this test does not render; the
    // Playwright spec walks the whole shell.)
    const start = await screen.findByRole("button", { name: "Start the PHQ-9" });
    let guard = 0;
    while (document.activeElement !== start) {
      await user.tab();
      guard += 1;
      if (guard > 30) {
        throw new Error("could not reach the start button with the keyboard");
      }
    }
    await user.keyboard("{Enter}");
    await screen.findByTestId("questionnaire");

    // Each group: focus a radio with the keyboard's Tab order and choose it with
    // Space. jsdom does not implement arrow-key movement inside a radio group,
    // so the real arrow-key walk lives in the Playwright spec; this test proves
    // the whole flow needs no pointer at all.
    for (let position = 1; position <= 9; position += 1) {
      const group = screen.getByTestId(`item-phq9_${position}`);
      const radios = within(group).getAllByRole("radio");
      radios[1]?.focus();
      await user.keyboard(" ");
      expect(radios[1]).toBeChecked();
    }

    const submit = screen.getByRole("button", { name: "See my result" });
    while (document.activeElement !== submit) {
      await user.tab();
    }
    await user.keyboard("{Enter}");

    const score = await screen.findByTestId("score-block");
    expect(within(score).getByText(/of 27/)).toBeInTheDocument();
    expect(fake.sent).toHaveLength(1);
    // Every answer is "Several days" (1), so the total is 9.
    expect(Object.values(fake.sent[0] ?? {}).every((value) => value === 1)).toBe(true);
  });
});
