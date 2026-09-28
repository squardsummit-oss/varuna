import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { boardPlacements, buildPumpBoard, placementsKey } from "@/app/pumps/pump-board-state";
import type { PricedPlan, PumpPlan } from "@/lib/api/pumps";

vi.mock("@/components/varuna/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("@/components/varuna/cycle-picker", () => ({ CyclePicker: () => null }));
vi.mock("sonner", () => ({ toast: vi.fn() }));
// NumberFlow's custom element does not upgrade in jsdom; the figure is asserted, not the roll.
vi.mock("@number-flow/react", async () => {
  const React = await import("react");
  return {
    default: ({ value }: { value: number }) =>
      React.createElement("span", { "data-number-flow": "", "data-value": String(value) }, value),
  };
});

const loadPumpPlan = vi.fn<() => Promise<PumpPlan | null>>();
const pricePumpPlacements = vi.fn<(input: unknown) => Promise<PricedPlan>>();
vi.mock("@/lib/api/pumps", () => ({
  loadPumpPlan: () => loadPumpPlan(),
  pricePumpPlacements: (input: unknown) => pricePumpPlacements(input),
  loadPumpMap: () => Promise.resolve(null),
}));
vi.mock("@/components/pumps/dispatch-map", () => ({ DispatchMap: () => null }));

const dispatchPumps = vi.fn();
vi.mock("@/lib/api/ops", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/ops")>();
  return { ...actual, dispatchPumps: (input: unknown) => dispatchPumps(input) };
});

import { PumpsScreen } from "@/app/pumps/pumps-screen";
import { clearPassphrase, writePassphrase } from "@/lib/api/ops";
import { toast } from "sonner";

const PLAN: PumpPlan = {
  runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
  thresholdCm: 45,
  benefitLabel: "Bathtub estimate, not a physics run",
  inventory: "synthetic",
  pumps: [
    { id: "P-05", capacityM3PerHour: 600, depot: "Municipal Garage, Worli", status: "available" },
    { id: "P-08", capacityM3PerHour: 600, depot: "Kurla Depot (BEST)", status: "available" },
  ],
  assignments: [
    {
      pumpId: "P-05",
      capacityM3PerHour: 600,
      depot: "Municipal Garage, Worli",
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
      depot: "Kurla Depot (BEST)",
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

/** The API's price for both pumps on Jijamata Road: the second buys only its marginal share. */
const PRICED: PricedPlan = {
  runId: PLAN.runId,
  placements: [
    {
      pumpId: "P-05",
      targetId: "street:Jijamata Road",
      targetName: "Jijamata Road",
      depot: "Municipal Garage, Worli",
      etaMin: 50,
      minutesSaved: 40,
      note: null,
    },
    {
      pumpId: "P-08",
      targetId: "street:Jijamata Road",
      targetName: "Jijamata Road",
      depot: "Kurla Depot (BEST)",
      etaMin: 35,
      minutesSaved: 5,
      note: null,
    },
  ],
  targets: [
    {
      targetId: "street:Jijamata Road",
      targetName: "Jijamata Road",
      minutesBefore: 100,
      minutesAfter: 55,
      minutesSaved: 45,
    },
  ],
  refused: [],
  totalMinutesSaved: 45,
  benefitModel: "emulator",
  benefitLabel: "Flash-lite emulator re-run with the pump's outflow",
  priceMs: 163,
};

describe("buildPumpBoard with a price", () => {
  const moved = { "P-08": "street:Jijamata Road" };

  it("lists the board's placements in a stable order and keys them", () => {
    const placements = boardPlacements(PLAN, { applied: true, moved });
    expect(placements).toEqual([
      { pumpId: "P-05", targetId: "street:Jijamata Road" },
      { pumpId: "P-08", targetId: "street:Jijamata Road" },
    ]);
    expect(placementsKey(placements)).toBe("P-05>street:Jijamata Road|P-08>street:Jijamata Road");
    // A pump dragged to a column this cycle does not have is in the pool, not placed.
    expect(boardPlacements(PLAN, { applied: false, moved: { "P-05": "street:Gone" } })).toEqual([]);
  });

  it("draws the API's price once it has one: columns, ETAs and the order", () => {
    const board = buildPumpBoard(PLAN, { applied: true, moved, priced: PRICED });
    const jijamata = board.columns.find((c) => c.id === "street:Jijamata Road");
    expect(jijamata?.minutesAbove45).toEqual({ before: 100, after: 55 });
    expect(jijamata?.pumps.map((p) => [p.id, p.etaMinutes])).toEqual([
      ["P-05", 50],
      ["P-08", 35],
    ]);
    // The column the operator emptied saves nothing now.
    const feet = board.columns.find((c) => c.id === "street:90 Feet Road");
    expect(feet?.minutesAbove45).toEqual({ before: 95, after: 95 });
    expect(board.order?.moves.map((m) => [m.pumpId, m.to, m.minutesAvoided])).toEqual([
      ["P-05", "Jijamata Road", 40],
      ["P-08", "Jijamata Road", 5],
    ]);
  });
});

describe("PumpsScreen dispatch", () => {
  beforeEach(() => {
    loadPumpPlan.mockResolvedValue(PLAN);
    pricePumpPlacements.mockResolvedValue(PRICED);
    dispatchPumps.mockResolvedValue({
      runId: PLAN.runId,
      city: "mumbai",
      dispatchedBy: "control room",
      dispatchedTs: "2026-09-22T16:00:00+05:30",
      orders: [{ pumpId: "P-05" }, { pumpId: "P-08" }],
      benefitLabel: "Flash-lite emulator re-run with the pump's outflow",
      syntheticInventory: true,
      notes: [
        "The pump inventory is synthetic: this order is recorded in the ops log and no lorry is called.",
      ],
      alertInstructions: ["Pump P-05 dispatched.", "Pump P-08 dispatched."],
      phoneMessages: [
        {
          hotspotId: "street:Jijamata Road",
          alertId: "A1",
          instruction: "Pump P-05 dispatched.",
          text: "VARUNA severe alert (exercise)\nJijamata Road: depth above 45 cm.\nPump P-05 dispatched.",
        },
      ],
    });
  });

  afterEach(() => {
    clearPassphrase();
    vi.clearAllMocks();
  });

  async function optimised() {
    render(<PumpsScreen />);
    fireEvent.click(screen.getByRole("tab", { name: "Plan board" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Optimise" })).toBeEnabled());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Optimise" }));
    });
    return screen.getByRole("button", { name: "Dispatch pumps" });
  }

  it("records the order and shows the alert instruction and the phone message", async () => {
    writePassphrase("monsoon desk 2026");
    const button = await optimised();
    await act(async () => {
      fireEvent.click(button);
    });
    await waitFor(() => expect(dispatchPumps).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Pump P-08 dispatched.")).toBeInTheDocument();
    const phone = screen.getByRole("figure", { name: "Ward officer's phone" });
    expect(within(phone).getByText(/Jijamata Road: depth above 45 cm/)).toBeInTheDocument();
    expect(toast).toHaveBeenCalledWith("Pumps dispatched", expect.anything());
  });

  it("sends nothing without a passphrase, and says where to enter it", async () => {
    const button = await optimised();
    await act(async () => {
      fireEvent.click(button);
    });
    expect(dispatchPumps).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(
      "Dispatched nothing",
      expect.objectContaining({ description: expect.stringMatching(/authority desk/) }),
    );
  });
});
