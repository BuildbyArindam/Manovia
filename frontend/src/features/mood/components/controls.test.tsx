/**
 * The three controls, tested for the properties that make them usable: real form
 * semantics, a visible word for every state, keyboard operation, and no axe
 * violations.
 *
 * These are the parts of the mood feature a hand-rolled widget usually gets
 * wrong — a div pretending to be a radio, a colour-only scale, a chip that
 * silently refuses the fourth tap — so each one is pinned here rather than only
 * through the page.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { useState, type ReactElement } from "react";

import { VALENCE_POINTS } from "../taxonomy";
import { EnergyScale } from "./EnergyScale";
import { FaceScale } from "./FaceScale";
import { TagChips } from "./TagChips";

function FaceHarness(): ReactElement {
  const [value, setValue] = useState<number | null>(null);
  return (
    <FaceScale
      value={value}
      onChange={setValue}
      legend="How do you feel?"
      hint="Pick the closest face."
    />
  );
}

function ChipsHarness(): ReactElement {
  const [selected, setSelected] = useState<string[]>([]);
  return (
    <TagChips
      options={[
        { id: "a", label: "Alpha" },
        { id: "b", label: "Beta" },
        { id: "c", label: "Gamma" },
        { id: "d", label: "Delta" },
      ]}
      selected={selected}
      onChange={setSelected}
      legend="What is here?"
      hint="Up to three."
      fullHint="That is three."
      max={3}
      name="test-chips"
    />
  );
}

describe("FaceScale", () => {
  it("is a radio group with a word for every face", () => {
    render(<FaceHarness />);

    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(5);
    for (const point of VALENCE_POINTS) {
      expect(screen.getByText(point.label)).toBeInTheDocument();
    }
    expect(screen.getAllByTestId("mood-face")).toHaveLength(5);
  });

  it("shows five different face shapes, so meaning is never colour-only", () => {
    render(<FaceHarness />);
    const paths = screen
      .getAllByTestId("mood-face")
      .map((svg) => svg.querySelector("path")?.getAttribute("d") ?? "");

    expect(new Set(paths).size).toBe(5);
  });

  it("starts with nothing chosen — the app does not answer this question", () => {
    render(<FaceHarness />);

    expect(
      screen.getAllByRole("radio").every((radio) => !(radio as HTMLInputElement).checked),
    ).toBe(true);
  });

  it.each(["Very low", "Low", "In the middle", "Good", "Very good"])(
    "reaches %s with the keyboard alone, using Space and the arrow keys",
    async (label) => {
      const user = userEvent.setup();
      render(<FaceHarness />);

      // Natural radio behaviour: tab in, Space to choose, arrows to move within
      // the group. Nothing here is a custom key handler.
      await user.tab();
      expect(screen.getByRole("radio", { name: "Very low" })).toHaveFocus();
      await user.keyboard(" ");
      const steps = VALENCE_POINTS.findIndex((point) => point.label === label);
      for (let step = 0; step < steps; step += 1) {
        await user.keyboard("{ArrowRight}");
      }

      expect(screen.getByRole("radio", { name: label })).toBeChecked();
    },
  );

  it("has no axe violations", async () => {
    const { container } = render(<FaceHarness />);

    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("EnergyScale", () => {
  it("is a labelled slider whose value is a word, not just a number", () => {
    // jsdom does not implement arrow keys on a range input (no native stepping),
    // so the *keyboard* half of this is proven in the Playwright spec against a
    // real browser (frontend/e2e/mood.spec.ts). What is asserted here is the
    // contract the browser will drive: the label, the current word, the readout.
    function Harness(): ReactElement {
      const [value, setValue] = useState(3);
      return <EnergyScale value={value} onChange={setValue} legend="Energy" hint="Move it." />;
    }
    render(<Harness />);

    const slider = screen.getByRole("slider", { name: "Energy" });
    expect(slider).toHaveAttribute("aria-valuetext", "Some");
    expect(slider).toHaveValue("3");

    fireEvent.change(slider, { target: { value: "4" } });

    expect(screen.getByRole("slider", { name: "Energy" })).toHaveAttribute(
      "aria-valuetext",
      "Good",
    );
    expect(screen.getByTestId("mood-energy-readout")).toHaveTextContent("4 — Good");
  });

  it("can be reached by keyboard at all (the slider is in the tab order)", async () => {
    const user = userEvent.setup();
    render(<EnergyScale value={3} onChange={() => undefined} legend="Energy" hint="Move it." />);

    await user.tab();

    expect(screen.getByRole("slider")).toHaveFocus();
  });

  it("shows all five words under the track, with the current one marked", () => {
    render(<EnergyScale value={2} onChange={() => undefined} legend="Energy" hint="Move it." />);

    for (const label of ["Empty", "Running low", "Some", "Good", "Plenty"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    const marked = document.querySelector('[data-current="true"]');
    expect(marked).toHaveTextContent("Running low");
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <EnergyScale value={3} onChange={() => undefined} legend="Energy" hint="Move it." />,
    );

    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("TagChips", () => {
  it("toggles a chip with Space, the way a checkbox should", async () => {
    const user = userEvent.setup();
    render(<ChipsHarness />);

    const alpha = screen.getByRole("checkbox", { name: "Alpha" });
    alpha.focus();
    await user.keyboard(" ");

    expect(alpha).toBeChecked();
    expect(screen.getByTestId("chips-test-chips-cap")).toHaveTextContent("1 of 3 chosen");
  });

  it("stops at the cap and explains why, instead of ignoring a tap", async () => {
    const user = userEvent.setup();
    render(<ChipsHarness />);

    for (const label of ["Alpha", "Beta", "Gamma"]) {
      await user.click(screen.getByRole("checkbox", { name: label }));
    }

    const delta = screen.getByRole("checkbox", { name: "Delta" });
    expect(delta).toBeDisabled();
    expect(screen.getByTestId("chips-test-chips-cap")).toHaveTextContent("That is three.");

    // Unchecking one frees the slot again, and nothing was dropped behind our back.
    await user.click(screen.getByRole("checkbox", { name: "Beta" }));
    expect(screen.getByRole("checkbox", { name: "Delta" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "Alpha" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Gamma" })).toBeChecked();
  });

  it("has no axe violations, including at the cap", async () => {
    const user = userEvent.setup();
    const { container } = render(<ChipsHarness />);

    for (const label of ["Alpha", "Beta", "Gamma"]) {
      await user.click(screen.getByRole("checkbox", { name: label }));
    }

    expect(await axe(container)).toHaveNoViolations();
  });
});
