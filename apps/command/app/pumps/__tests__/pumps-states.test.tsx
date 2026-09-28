import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PumpCycles, PumpMap, PumpPlan, RawPumpMap } from "@/lib/api/pumps";

// Two cycles of the same storm, so the screen can be driven there and back (A, B, A).
const RUNS = vi.hoisted(() => ({
  a: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
  b: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-baked",
}));

vi.mock("@/components/varuna/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => children,
}));
// The picker's own list is its test; here it is two buttons that pick a cycle the way it does.
vi.mock("@/components/varuna/cycle-picker", () => ({
  CyclePicker: ({ onPick }: { onPick: (runId: string) => void }) => (
    <div>
      <button type="button" onClick={() => onPick(RUNS.a)}>
        Pick 08:40
      </button>
      <button type="button" onClick={() => onPick(RUNS.b)}>
        Pick 08:10
      </button>
    </div>
  ),
}));
vi.mock("sonner", () => ({ toast: vi.fn() }));
vi.mock("@number-flow/react", async () => {
  const React = await import("react");
  return {
    default: ({ value, suffix = "" }: { value: number; suffix?: string }) =>
      React.createElement("span", { "data-number-flow": "" }, `${value}${suffix}`),
  };
});

const loadPumpPlan = vi.fn<(runId?: string) => Promise<PumpPlan | null>>();
const loadPumpCycles = vi.fn<() => Promise<PumpCycles>>();
const loadPumpMap = vi.fn<(runId?: string) => Promise<PumpMap | null>>();
vi.mock("@/lib/api/pumps", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/pumps")>();
  return {
    ...actual,
    loadPumpPlan: (runId?: string) => loadPumpPlan(runId),
    loadPumpMap: (runId?: string) => loadPumpMap(runId),
    loadPumpCycles: () => loadPumpCycles(),
    pricePumpPlacements: () => new Promise(() => {}),
  };
});
// The map is WebGL. Here it does the one thing the screen relies on: it starts the dispatch clock
// on its first frame, as the deck layers do.
vi.mock("@/components/pumps/dispatch-map", async () => {
  const React = await import("react");
  return {
    DispatchMap: (p: { clock?: { idle: boolean; start: () => number } | null }) => {
      React.useEffect(() => {
        if (p.clock && !p.clock.idle) p.clock.start();
      }, [p.clock]);
      return React.createElement("section", { "aria-label": "Dispatch map" });
    },
  };
});

import { PumpsScreen } from "@/app/pumps/pumps-screen";
import { arriveMs } from "@/components/pumps/dispatch-clock";
import { dispatchOrder } from "@/components/pumps/model";
import { parsePumpMap } from "@/lib/api/pumps";

// `GET /v1/pumps/map` for the 08:40 demo cycle, as the API served it (see pump-map.test.tsx).
import fixture from "./pump-map.fixture.json";

const MAP_A = parsePumpMap(fixture as unknown as RawPumpMap);
const MAP_B: PumpMap = { ...MAP_A, runId: RUNS.b };

/** The plan `GET /v1/pumps` serves for a cycle, rebuilt from the map's own legs. */
function planFor(runId: string): PumpPlan {
  return {
    runId,
    thresholdCm: MAP_A.thresholdCm,
    benefitLabel: MAP_A.benefitLabel,
    inventory: MAP_A.inventory,
    pumps: MAP_A.legs.map((l) => ({
      id: l.pumpId,
      capacityM3PerHour: l.capacityM3PerHour,
      depot: l.depot.name,
      status: "available",
    })),
    assignments: MAP_A.legs.map((l) => ({
      pumpId: l.pumpId,
      capacityM3PerHour: l.capacityM3PerHour,
      depot: l.depot.name,
      targetId: l.target.id,
      targetName: l.target.name,
      lon: l.target.lon,
      lat: l.target.lat,
      etaMin: l.etaMin,
      minutesBefore: l.minutesBefore,
      minutesAfter: l.minutesAfter,
      minutesSaved: l.minutesSaved,
    })),
    unassigned: [],
    totalMinutesSaved: MAP_A.summary.minutesSaved,
  };
}

beforeEach(() => {
  // No cycle list: the screen opens on the newest run, which the API answers as cycle A.
  loadPumpCycles.mockRejectedValue(new Error("offline"));
  loadPumpPlan.mockImplementation((runId) => Promise.resolve(planFor(runId ?? RUNS.a)));
  loadPumpMap.mockImplementation((runId) => Promise.resolve(runId === RUNS.b ? MAP_B : MAP_A));
});

afterEach(() => {
  vi.useRealTimers();
  loadPumpPlan.mockReset();
  loadPumpCycles.mockReset();
  loadPumpMap.mockReset();
});

/** Settles the fetches and the dispatch clock's start, which it reports in a microtask. */
async function settle() {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

/** The rail's row for the pump that goes to Lokmanya Tilak Nagar: 115 min, 15 with P-08. */
function firstGauge(): HTMLElement {
  return within(screen.getByRole("list", { name: "Places the pumps go" })).getAllByRole(
    "button",
  )[0];
}

describe("Jalayantra keeps the gauges in step with the lorries (M34 with M33)", () => {
  it("never hands a dispatch on a revisited cycle the start time of an earlier one", async () => {
    render(<PumpsScreen />);
    await screen.findByRole("list", { name: "Places the pumps go" });
    // Timers and `performance.now()` are faked from here, so the drive is timed exactly.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "performance"] });
    const p08 = dispatchOrder(MAP_A).get("P-08") ?? 0;
    expect(firstGauge()).toHaveTextContent("Lokmanya Tilak Nagar");
    expect(firstGauge()).toHaveTextContent("115 min");

    // First dispatch on 08:40: every pump arrives and every gauge drains.
    fireEvent.click(screen.getByRole("button", { name: "Optimise" }));
    await settle();
    expect(firstGauge().querySelector("[data-number-flow]")).toHaveTextContent("115 min");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(arriveMs(MAP_A.legs.length) + 5000);
    });
    expect(firstGauge().querySelector("[data-number-flow]")).toHaveTextContent("15 min");

    // Away to 08:10 and back to 08:40: the plan is unsent again, every gauge full.
    fireEvent.click(screen.getByRole("button", { name: "Pick 08:10" }));
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Pick 08:40" }));
    await settle();
    expect(firstGauge()).toHaveTextContent("115 min");

    // The second dispatch starts now, not when the first one did: at its first instant no pump
    // is there, and P-08's gauge drains only when P-08 reaches its place on the map.
    fireEvent.click(screen.getByRole("button", { name: "Optimise" }));
    await settle();
    expect(firstGauge().querySelector("[data-number-flow]")).toHaveTextContent("115 min");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(arriveMs(p08) - 50);
    });
    expect(firstGauge().querySelector("[data-number-flow]")).toHaveTextContent("115 min");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(firstGauge().querySelector("[data-number-flow]")).toHaveTextContent("15 min");
  });
});

describe("the plan board while the dispatch map is not there", () => {
  async function openBoard() {
    render(<PumpsScreen />);
    fireEvent.click(screen.getByRole("tab", { name: "Plan board" }));
    await screen.findByRole("region", { name: "Pump board" });
  }

  it("says each column's depth series is loading while it is", async () => {
    loadPumpMap.mockImplementation(() => new Promise(() => {}));
    await openBoard();

    await screen.findAllByText("Loading this place's depth series");
    expect(screen.queryByText("No depth series for this place in this run")).toBeNull();
  });

  it("says the series did not load, and prints why above the board", async () => {
    loadPumpMap.mockRejectedValue(
      new Error(
        "The VARUNA API did not answer at http://localhost:8000, so there is no dispatch map to show. Reload once it is back; on this laptop, make dev starts it.",
      ),
    );
    await openBoard();

    // dnd-kit keeps its own live region, so the reason is found by its words.
    const reason = await screen.findByText(/so there is no dispatch map to show/);
    expect(reason).toHaveAttribute("role", "status");
    expect(reason).toHaveTextContent(
      /^The VARUNA API did not answer at .+, so there is no dispatch map to show\..*The plan's own figures still stand\.$/,
    );
    expect(
      screen.getAllByText("Depth series did not load; the reason is above the board").length,
    ).toBe(new Set(MAP_A.legs.map((l) => l.target.id)).size);
    expect(screen.queryByText("No depth series for this place in this run")).toBeNull();
    expect(screen.queryByText("Loading this place's depth series")).toBeNull();
  });

  it("says a settled answer with no map in it carries no series, and why", async () => {
    loadPumpMap.mockResolvedValue(null);
    await openBoard();

    const reason = await screen.findByText(/^The API returned no dispatch map for this cycle/);
    expect(reason).toHaveAttribute("role", "status");
    expect(screen.getAllByText("No depth series for this place in this run").length).toBe(
      new Set(MAP_A.legs.map((l) => l.target.id)).size,
    );
    expect(screen.queryByText(/did not load/)).toBeNull();
  });

  it("captions every column with its own peak once the map is there", async () => {
    await openBoard();

    await screen.findAllByText(/^Peak \d+ cm with no pump, next 3 h$/);
    expect(screen.queryByText("No depth series for this place in this run")).toBeNull();
    expect(screen.queryByText("Loading this place's depth series")).toBeNull();
    expect(screen.queryByText(/dispatch map/)).toBeNull();
  });
});
