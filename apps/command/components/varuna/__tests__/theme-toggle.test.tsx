import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ThemeToggle } from "@/components/varuna/theme-toggle";
import { THEME_STORAGE_KEY, __resetThemeForTests } from "@/lib/theme";

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  __resetThemeForTests();
});

describe("ThemeToggle", () => {
  it("offers light mode from the dark default", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: "Switch to light mode" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(button).toHaveAttribute("type", "button");
  });

  it("switches the document and remembers the choice", () => {
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: "Switch to light mode" }));
    const button = screen.getByRole("button", { name: "Switch to dark mode" });
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    fireEvent.click(button);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument();
  });

  it("is a 44 px target at size md and 32 px at sm", () => {
    const { rerender } = render(<ThemeToggle size="md" />);
    expect(screen.getByRole("button").className).toContain("size-11");
    rerender(<ThemeToggle size="sm" className="ml-1" />);
    expect(screen.getByRole("button").className).toContain("size-8");
    expect(screen.getByRole("button").className).toContain("ml-1");
  });

  it("keeps every toggle on the page in step", () => {
    render(
      <>
        <ThemeToggle />
        <ThemeToggle size="md" />
      </>,
    );
    fireEvent.click(screen.getAllByRole("button")[0]);
    for (const button of screen.getAllByRole("button")) {
      expect(button).toHaveAccessibleName("Switch to dark mode");
    }
  });
});
