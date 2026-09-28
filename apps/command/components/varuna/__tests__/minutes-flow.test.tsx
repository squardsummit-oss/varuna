import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MinutesFlow, minuteParts } from "@/components/varuna/minutes-flow";
import { formatMinutes, MISSING } from "@/lib/format";

// NumberFlow draws into a shadow root jsdom cannot read; this stand-in prints the value it was
// given, which is exactly what the rolling element settles on.
vi.mock("@number-flow/react", async () => {
  const React = await import("react");
  return {
    default: ({ value }: { value: number }) =>
      React.createElement("span", { "data-number-flow": "", "data-value": String(value) }, value),
  };
});

const originalMatchMedia = window.matchMedia;

function prefersReducedMotion(on: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: on && query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

const VALUES = [0, 5, 45, 59.6, 60, 100, 120, 125];

describe("minuteParts", () => {
  it("splits minutes the way formatMinutes prints them", () => {
    expect(minuteParts(45)).toEqual({ hours: 0, rest: 45, showHours: false, showMinutes: true });
    expect(minuteParts(120)).toEqual({ hours: 2, rest: 0, showHours: true, showMinutes: false });
    expect(minuteParts(100)).toEqual({ hours: 1, rest: 40, showHours: true, showMinutes: true });
    expect(minuteParts(-3)).toEqual({ hours: 0, rest: 0, showHours: false, showMinutes: true });
  });
});

describe("MinutesFlow", () => {
  it.each(VALUES)("rolls to a string identical to formatMinutes(%s)", (value) => {
    prefersReducedMotion(false);
    const { container } = render(<MinutesFlow value={value} />);
    expect(container.querySelectorAll("[data-number-flow]").length).toBeGreaterThan(0);
    expect(container.textContent).toBe(formatMinutes(value));
  });

  it.each(VALUES)("renders plain text under reduced motion for %s", (value) => {
    prefersReducedMotion(true);
    const { container } = render(<MinutesFlow value={value} />);
    expect(container.querySelectorAll("[data-number-flow]")).toHaveLength(0);
    expect(container.textContent).toBe(formatMinutes(value));
  });

  it("keeps the minutes element when the value changes, so it rolls rather than remounts", () => {
    prefersReducedMotion(false);
    const { container, rerender } = render(<MinutesFlow value={100} />);
    const flows = container.querySelectorAll("[data-number-flow]");
    const minutes = flows[flows.length - 1];
    expect(minutes).toHaveAttribute("data-value", "40");

    rerender(<MinutesFlow value={0} />);
    expect(minutes.isConnected).toBe(true);
    expect(minutes).toHaveAttribute("data-value", "0");
    expect(container.textContent).toBe("0 min");
  });

  it("prints the missing mark for a value the API did not send", () => {
    prefersReducedMotion(false);
    const { container } = render(<MinutesFlow value={null} />);
    expect(container.textContent).toBe(MISSING);
  });
});
