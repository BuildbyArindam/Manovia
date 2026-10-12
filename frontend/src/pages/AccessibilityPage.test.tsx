import { fireEvent, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { beforeEach, describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/utils";
import { CONTRAST_STORAGE_KEY } from "@/theme/ThemeProvider";

import { AccessibilityPage } from "./AccessibilityPage";

describe("AccessibilityPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-contrast");
  });

  it("renders WCAG 2.2 AA conformance details, toggles high-contrast mode, and has no axe violations", async () => {
    const { container } = renderWithProviders(<AccessibilityPage />);

    expect(
      screen.getByRole("heading", { name: "Accessibility statement" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /Conformance status/ }),
    ).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();

    const highRadio = screen.getByRole("radio", { name: /High contrast/ });
    fireEvent.click(highRadio);

    expect(document.documentElement.getAttribute("data-contrast")).toBe("high");
    expect(window.localStorage.getItem(CONTRAST_STORAGE_KEY)).toBe("high");
    expect(screen.getByTestId("a11y-active-state")).toHaveTextContent(/Contrast: high/);
    expect(await axe(container)).toHaveNoViolations();
  });
});
