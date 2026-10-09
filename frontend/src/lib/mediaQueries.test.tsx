/**
 * Media-query hooks.
 *
 * These are what make "respects prefers-reduced-motion" and "bottom nav on
 * mobile, side nav on desktop" testable: both behaviours are driven by a
 * subscription to a media query rather than by a CSS rule the test cannot see.
 */

import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ReactElement } from "react";

import { useMediaQuery } from "@/lib/useMediaQuery";
import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";
import { stubMatchMedia } from "@/test/matchMedia";

function Probe({ query }: { query: string }): ReactElement {
  const matches = useMediaQuery(query);
  return <span data-testid="probe">{matches ? "yes" : "no"}</span>;
}

function MotionProbe(): ReactElement {
  const reduce = usePrefersReducedMotion();
  return <span data-testid="motion">{reduce ? "reduced" : "full"}</span>;
}

describe("useMediaQuery", () => {
  it("reports the current match", () => {
    stubMatchMedia(true);

    render(<Probe query="(min-width: 768px)" />);

    expect(screen.getByTestId("probe")).toHaveTextContent("yes");
  });

  it("reports no match, and updates when the query changes", () => {
    const media = stubMatchMedia(false);

    render(<Probe query="(min-width: 768px)" />);
    expect(screen.getByTestId("probe")).toHaveTextContent("no");

    act(() => {
      media.emit(true);
    });
    expect(screen.getByTestId("probe")).toHaveTextContent("yes");
  });

  it("falls back to the default when matchMedia is unavailable", () => {
    vi.stubGlobal("matchMedia", undefined);

    render(<Probe query="(min-width: 768px)" />);

    expect(screen.getByTestId("probe")).toHaveTextContent("no");
  });
});

describe("usePrefersReducedMotion", () => {
  it("is true when the OS asks for reduced motion", () => {
    stubMatchMedia(true);

    render(<MotionProbe />);

    expect(screen.getByTestId("motion")).toHaveTextContent("reduced");
  });

  it("is false otherwise", () => {
    stubMatchMedia(false);

    render(<MotionProbe />);

    expect(screen.getByTestId("motion")).toHaveTextContent("full");
  });
});
