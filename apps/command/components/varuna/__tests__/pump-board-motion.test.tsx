import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PumpBoard, pumpCardMotion, type PumpColumn } from "@/components/varuna/pump-board";
import type { Pump } from "@/components/varuna/pump-card";
import { M } from "@/lib/motion";

// The card's motion props are what M17 is; a `motion.div` swallows them, so this stand-in writes
// them onto the DOM where the test can read what the board actually passed.
vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("motion/react")>();
  const React = await import("react");
  const Div = React.forwardRef<HTMLDivElement, Record<string, unknown>>(function Div(props, ref) {
    const { layoutId, transition, ...rest } = props;
    // Framer-only props a real DOM div would warn about.
    delete rest.layout;
    delete rest.layoutScroll;
    return React.createElement("div", {
      ...rest,
      ref,
      "data-layout-id": typeof layoutId === "string" ? layoutId : "",
      "data-transition": JSON.stringify(transition ?? null),
    });
  });
  return {
    ...actual,
    motion: new Proxy(actual.motion, {
      get: (target, key) => (key === "div" ? Div : Reflect.get(target, key)),
    }),
  };
});

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

const PUMP: Pump = {
  id: "P-05",
  capacityM3PerHour: 900,
  depot: "F/South ward office",
  status: "available",
  etaMinutes: null,
};

const COLUMN: PumpColumn = {
  id: "street:Jijamata Road",
  title: "Jijamata Road",
  pumps: [],
  minutesAbove45: { before: 100, after: 0 },
};

const noop = () => {};

function cardWrapper(pumpId: string): HTMLElement {
  const card = screen.getByRole("article", { name: `Pump ${pumpId}` });
  const wrapper = card.closest<HTMLElement>("[data-layout-id]");
  if (!wrapper) throw new Error("card has no motion wrapper");
  return wrapper;
}

function benefitParagraph(): HTMLElement {
  const p = screen.getByText(/Minutes above 45 cm/).closest("p");
  if (!p) throw new Error("no benefit line");
  return p;
}

describe("pumpCardMotion", () => {
  it("flies on a shared layout id with the catalogue's M17 transition", () => {
    expect(pumpCardMotion("P-05", false)).toEqual({
      layoutId: "pump-P-05",
      transition: M.M17.full?.transition,
    });
    expect(M.M17.full?.transition).toMatchObject({ duration: 0.3 });
  });

  it("moves instantly under reduced motion: no layout id, zero duration", () => {
    expect(pumpCardMotion("P-05", true)).toEqual({
      layoutId: undefined,
      transition: { duration: 0 },
    });
  });
});

describe("PumpBoard motion M17", () => {
  it("gives each card its layout id and the M17 transition", () => {
    prefersReducedMotion(false);
    render(<PumpBoard pumps={[PUMP]} columns={[COLUMN]} onAssign={noop} />);

    const wrapper = cardWrapper("P-05");
    expect(wrapper).toHaveAttribute("data-layout-id", "pump-P-05");
    expect(JSON.parse(wrapper.getAttribute("data-transition") ?? "null")).toEqual(
      JSON.parse(JSON.stringify(M.M17.full?.transition)),
    );
  });

  it("gives cards a zero-duration transition and no layout id under reduced motion", () => {
    prefersReducedMotion(true);
    render(<PumpBoard pumps={[PUMP]} columns={[COLUMN]} onAssign={noop} />);

    const wrapper = cardWrapper("P-05");
    expect(wrapper).toHaveAttribute("data-layout-id", "");
    expect(JSON.parse(wrapper.getAttribute("data-transition") ?? "null")).toEqual({ duration: 0 });
  });

  it("rolls the column's figure from the no-pump value to the plan's in the same element", () => {
    prefersReducedMotion(false);
    const { rerender } = render(
      <PumpBoard pumps={[PUMP]} columns={[COLUMN]} planApplied={false} />,
    );
    const line = benefitParagraph();
    // "1 h 40 min" is two flows, hours then minutes; the minutes flow is the one that rolls to 0.
    const [hours, current] = line.querySelectorAll("[data-number-flow]");
    expect(hours).toHaveAttribute("data-value", "1");
    expect(current).toHaveAttribute("data-value", "40");
    expect(line.textContent).toBe("Minutes above 45 cm: 1 h 40 min with no pump sent");

    rerender(<PumpBoard pumps={[]} columns={[COLUMN]} planApplied />);
    // The same node, now at the plan's value: a roll, not a swap.
    expect(current?.isConnected).toBe(true);
    expect(current).toHaveAttribute("data-value", "0");
    expect(benefitParagraph().textContent).toBe(
      "Minutes above 45 cm: 0 min with the plan, 1 h 40 min without",
    );
  });

  it("prints the same figures as plain text under reduced motion", () => {
    prefersReducedMotion(true);
    render(<PumpBoard pumps={[]} columns={[COLUMN]} planApplied />);

    const line = benefitParagraph();
    expect(line.querySelectorAll("[data-number-flow]")).toHaveLength(0);
    expect(line.textContent).toBe("Minutes above 45 cm: 0 min with the plan, 1 h 40 min without");
  });
});
