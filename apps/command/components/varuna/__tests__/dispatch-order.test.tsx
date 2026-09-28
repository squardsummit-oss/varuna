import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DispatchOrder,
  describeMove,
  type DispatchOrderPlan,
} from "@/components/varuna/dispatch-order";

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

const plan = (etaMinutes: number, minutesAvoided: number): DispatchOrderPlan => ({
  runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
  moves: [
    {
      id: "P-05",
      pumpId: "P-05",
      from: "F/South ward office",
      to: "Jijamata Road",
      etaMinutes,
      minutesAvoided,
    },
  ],
});

describe("DispatchOrder", () => {
  it("reads exactly as describeMove, with rolling ETA and minutes", () => {
    prefersReducedMotion(false);
    const order = plan(50, 100);
    render(<DispatchOrder order={order} />);

    const line = screen.getByRole("listitem");
    expect(line.textContent).toBe(describeMove(order.moves[0]));
    expect(
      [...line.querySelectorAll("[data-number-flow]")].map((n) => n.getAttribute("data-value")),
    ).toEqual(["50", "1", "40"]);
  });

  it("rolls the figures in place when the plan changes", () => {
    prefersReducedMotion(false);
    const { rerender } = render(<DispatchOrder order={plan(50, 45)} />);
    const [eta, minutes] = screen.getByRole("listitem").querySelectorAll("[data-number-flow]");

    rerender(<DispatchOrder order={plan(27, 30)} />);
    expect(eta.isConnected && minutes.isConnected).toBe(true);
    expect(eta).toHaveAttribute("data-value", "27");
    expect(minutes).toHaveAttribute("data-value", "30");
  });

  it("is the plain sentence under reduced motion", () => {
    prefersReducedMotion(true);
    const order = plan(50, 100);
    render(<DispatchOrder order={order} />);

    const line = screen.getByRole("listitem");
    expect(line.querySelectorAll("[data-number-flow]")).toHaveLength(0);
    expect(line.textContent).toBe(
      "Move P-05 from F/South ward office to Jijamata Road now; ETA 50 min; prevents about 1 h 40 min above 45 cm.",
    );
  });

  it("asks for Optimise when there is no order", () => {
    render(<DispatchOrder order={null} />);
    expect(screen.getByText("No dispatch order yet")).toBeInTheDocument();
  });
});
