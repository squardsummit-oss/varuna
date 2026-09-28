import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PumpPlan } from "@/lib/api/pumps";

vi.mock("@/components/varuna/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("@/components/varuna/cycle-picker", () => ({ CyclePicker: () => null }));
vi.mock("sonner", () => ({ toast: vi.fn() }));
vi.mock("@number-flow/react", async () => {
  const React = await import("react");
  return {
    default: ({ value }: { value: number }) =>
      React.createElement("span", { "data-number-flow": "", "data-value": String(value) }, value),
  };
});

const loadPumpPlan = vi.fn<(runId?: string, signal?: AbortSignal) => Promise<PumpPlan | null>>();
vi.mock("@/lib/api/pumps", () => ({
  loadPumpPlan: (runId?: string, signal?: AbortSignal) => loadPumpPlan(runId, signal),
  // The dispatch map is its own test (pump-map.test.tsx); the board's tests do not wait for it.
  loadPumpMap: () => Promise.resolve(null),
}));
vi.mock("@/components/pumps/dispatch-map", () => ({ DispatchMap: () => null }));

import { PumpsScreen } from "@/app/pumps/pumps-screen";

/** The API's 08:40 figures for two of its twelve moves. */
const PLAN: PumpPlan = {
  runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
  thresholdCm: 45,
  benefitLabel: "bathtub estimate, not a physics run",
  inventory: "synthetic",
  pumps: [
    { id: "P-05", capacityM3PerHour: 900, depot: "F/South ward office", status: "available" },
    { id: "P-08", capacityM3PerHour: 600, depot: "G/North ward office", status: "available" },
  ],
  assignments: [
    {
      pumpId: "P-05",
      capacityM3PerHour: 900,
      depot: "F/South ward office",
      targetId: "street:Jijamata Road",
      targetName: "Jijamata Road",
      lon: null,
      lat: null,
      etaMin: 50,
      minutesBefore: 100,
      minutesAfter: 0,
      minutesSaved: 100,
    },
    {
      pumpId: "P-08",
      capacityM3PerHour: 600,
      depot: "G/North ward office",
      targetId: "street:90 Feet Road",
      targetName: "90 Feet Road",
      lon: null,
      lat: null,
      etaMin: 10,
      minutesBefore: 95,
      minutesAfter: 0,
      minutesSaved: 95,
    },
  ],
  unassigned: [],
  totalMinutesSaved: 195,
};

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

beforeEach(() => {
  loadPumpPlan.mockResolvedValue(PLAN);
});

afterEach(() => {
  window.matchMedia = originalMatchMedia;
  loadPumpPlan.mockReset();
});

function column(id: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`[data-column-id="${id}"]`);
  if (!el) throw new Error(`no column ${id}`);
  return el;
}

function benefitOf(id: string): HTMLElement {
  const p = within(column(id))
    .getByText(/Minutes above 45 cm/)
    .closest("p");
  if (!p) throw new Error("no benefit line");
  return p;
}

async function openBoard() {
  render(<PumpsScreen />);
  // The screen opens on the dispatch map; the kanban is the second view.
  fireEvent.click(screen.getByRole("tab", { name: "Plan board" }));
  // Re-queried inside waitFor: the board mounts under a DndContext once the plan arrives, so the
  // button present before the fetch is not the one that becomes enabled.
  await waitFor(() => expect(screen.getByRole("button", { name: "Optimise" })).toBeEnabled());
  return screen.getByRole("button", { name: "Optimise" });
}

describe("PumpsScreen Optimise (motion M17)", () => {
  it("rolls each column and writes the order with the numbers the API returned", async () => {
    prefersReducedMotion(false);
    const optimise = await openBoard();

    // Before: every pump in the pool, each column at its no-pump figure, no order.
    expect(within(column("__pool__")).getAllByRole("article")).toHaveLength(2);
    // "1 h 40 min" is an hours flow then a minutes flow; the minutes flow is the one that rolls.
    const [hours, figure] =
      benefitOf("street:Jijamata Road").querySelectorAll("[data-number-flow]");
    expect(hours).toHaveAttribute("data-value", "1");
    expect(figure).toHaveAttribute("data-value", "40");
    expect(benefitOf("street:Jijamata Road").textContent).toBe(
      "Minutes above 45 cm: 1 h 40 min with no pump sent",
    );
    expect(screen.getByText("No dispatch order yet")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(optimise);
    });

    // After: the card is in its column, the same figure node rolled to the plan's value, and the
    // order carries the API's ETA and minutes as rolling figures.
    expect(within(column("street:Jijamata Road")).getByRole("article", { name: "Pump P-05" }));
    expect(figure?.isConnected).toBe(true);
    expect(figure).toHaveAttribute("data-value", "0");
    expect(benefitOf("street:Jijamata Road").textContent).toBe(
      "Minutes above 45 cm: 0 min with the plan, 1 h 40 min without",
    );
    const lines = screen.getAllByRole("listitem");
    expect(
      [...lines[0].querySelectorAll("[data-number-flow]")].map((n) => n.getAttribute("data-value")),
    ).toEqual(["50", "1", "40"]);
    expect(screen.getByRole("button", { name: "Optimise" })).toBeDisabled();
  });

  it("moves instantly with plain numbers under reduced motion", async () => {
    prefersReducedMotion(true);
    const optimise = await openBoard();

    await act(async () => {
      fireEvent.click(optimise);
    });

    expect(within(column("street:90 Feet Road")).getByRole("article", { name: "Pump P-08" }));
    expect(document.querySelectorAll("[data-number-flow]")).toHaveLength(0);
    expect(benefitOf("street:90 Feet Road").textContent).toBe(
      "Minutes above 45 cm: 0 min with the plan, 1 h 35 min without",
    );
    expect(screen.getAllByRole("listitem")[1].textContent).toBe(
      "Move P-08 from G/North ward office to 90 Feet Road now; ETA 10 min; prevents about 1 h 35 min above 45 cm.",
    );
  });
});
