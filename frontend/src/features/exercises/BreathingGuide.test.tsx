import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import { describe, expect, it } from "vitest";

import { breathingExercise } from "@/test/exerciseFakes";

import { BreathingGuide, ORB_CLASSES } from "./BreathingGuide";
import { CUE_LABELS } from "./copy";
import type { StepCue } from "./types";

const HERE = dirname(fileURLToPath(import.meta.url));
const cues: StepCue[] = ["instruction", "inhale", "hold-in", "exhale", "hold-out"];

describe("breathing guide CSS regression", () => {
  it("retains every breathing selector and animation when Tailwind scans only the real component", async () => {
    // Real CSS, real Tailwind, no build artifacts and no test-file safelisting.
    // A dynamic `exercise-orb-${cue}` previously made all these rules vanish.
    const css = readFileSync(join(HERE, "..", "..", "index.css"), "utf8");
    const orbLayer = css.slice(
      css.lastIndexOf("@layer components {"),
      css.indexOf("@layer utilities {"),
    );
    const result = await postcss([
      tailwindcss({
        content: [join(HERE, "BreathingGuide.tsx")],
        corePlugins: [],
        theme: {},
      }),
    ]).process(`@tailwind components;\n${orbLayer}`, { from: undefined });
    for (const cue of cues) {
      expect(result.css).toContain(`.${ORB_CLASSES[cue]}`);
    }
    expect(result.css).toContain("@keyframes exercise-inhale");
    expect(result.css).toContain("@keyframes exercise-exhale");
  });

  it.each(cues)("keeps the %s cue in visible text outside the decorative SVG", (cue) => {
    const step = { ...breathingExercise().steps[0]!, cue };
    render(<BreathingGuide step={step} secondsLeft={4} paused={false} staticOnly={false} />);
    expect(screen.getByText(CUE_LABELS[cue])).toBeVisible();
    const svg = screen.getByTestId("animated-breathing-guide").querySelector("svg");
    expect(svg).toHaveClass(ORB_CLASSES[cue]);
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("timer")).toHaveAttribute("aria-live", "off");
  });
});
