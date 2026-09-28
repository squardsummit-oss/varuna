import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PumpCycles, PumpMap, PumpPlan } from "@/lib/api/pumps";

vi.mock("@/components/varuna/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("@/components/varuna/cycle-picker", () => ({ CyclePicker: () => null }));
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
vi.mock("@/lib/api/pumps", () => ({
  loadPumpPlan: (runId?: string) => loadPumpPlan(runId),
  loadPumpMap: (runId?: string) => loadPumpMap(runId),
  loadPumpCycles: () => loadPumpCycles(),
  pricePumpPlacements: () => new Promise(() => {}),
}));
// The map itself is WebGL; here it says what it was asked to show, so the states can be read.
vi.mock("@/components/pumps/dispatch-map", () => ({
  DispatchMap: (p: {
    error?: string | null;
    errorTitle?: string;
    emptyTitle?: string;
    emptyDescription?: string;
    emptyAction?: React.ReactNode;
  }) => (
    <section aria-label="Dispatch map">
      {p.error ? <h3>{p.errorTitle}</h3> : p.emptyTitle ? <h3>{p.emptyTitle}</h3> : null}
      <p>{p.error ?? p.emptyDescription}</p>
      {p.emptyAction}
    </section>
  ),
}));

import { PumpsScreen } from "@/app/pumps/pumps-screen";

const RUN = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";

const PLAN: PumpPlan = {
  runId: RUN,
  thresholdCm: 45,
  benefitLabel: "Flash-lite emulator re-run with the pump's outflow",
  inventory: "synthetic",
  pumps: [
    { id: "P-05", capacityM3PerHour: 600, depot: "Municipal Garage, Worli", status: "available" },
    { id: "P-08", capacityM3PerHour: 600, depot: "Kurla Depot (BEST)", status: "available" },
  ],
  assignments: [
    {
      pumpId: "P-08",
      capacityM3PerHour: 600,
      depot: "Kurla Depot (BEST)",
      targetId: "street:Lokmanya Tilak Nagar",
      targetName: "Lokmanya Tilak Nagar",
      lon: null,
      lat: null,
      etaMin: 7,
      minutesBefore: 115,
      minutesAfter: 15,
      minutesSaved: 100,
    },
    {
      pumpId: "P-05",
      capacityM3PerHour: 600,
      depot: "Municipal Garage, Worli",
      targetId: "street:Gurunanak Marg",
      targetName: "Gurunanak Marg",
      lon: null,
      lat: null,
      etaMin: 12,
      minutesBefore: 90,
      minutesAfter: 10,
      minutesSaved: 80,
    },
  ],
  unassigned: [],
  totalMinutesSaved: 180,
};

beforeEach(() => {
  loadPumpMap.mockResolvedValue(null);
});

afterEach(() => {
  loadPumpPlan.mockReset();
  loadPumpCycles.mockReset();
  loadPumpMap.mockReset();
});

const BUSIEST = "MUM-20190702T0310Z-busiest";

const CYCLES: PumpCycles = {
  busiestRunId: BUSIEST,
  cycles: [
    {
      runId: RUN,
      cycleTs: "2019-07-02T09:10:00+05:30",
      nAssigned: 1,
      nUnassigned: 0,
      minutesSaved: 25,
    },
    {
      runId: BUSIEST,
      cycleTs: "2019-07-02T08:40:00+05:30",
      nAssigned: 12,
      nUnassigned: 45,
      minutesSaved: 915,
    },
  ],
};

describe("Jalayantra's dispatch map view", () => {
  it("opens with the fleet at its depots and Optimise as its one action", async () => {
    loadPumpPlan.mockResolvedValue(PLAN);
    render(<PumpsScreen />);

    // The demo video presses this button by name, so it must be the only "Optimise" on the view.
    const optimise = await screen.findByRole("button", { name: "Optimise" });
    expect(screen.getAllByRole("button", { name: "Optimise" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Dispatch pumps" })).toBeNull();
    expect(screen.getByText("2 pumps wait at their depots.")).toBeInTheDocument();
    const strip = screen.getByRole("region", { name: "What the plan buys" });
    expect(within(strip).getByText("Not sent")).toBeInTheDocument();
    expect(within(strip).getByText("205 min")).toBeInTheDocument();
    expect(strip).toHaveTextContent(/2 of 2 pumps assigned; 180 min avoided at 2 places\./);

    await act(async () => {
      fireEvent.click(optimise);
    });

    // Sent: the plan's own figure, and the two things left to do with it.
    expect(within(strip).getByText("25 min")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dispatch pumps" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Optimise" })).toBeNull();
    // No roads loaded (the map is mocked empty), so there is nothing to replay.
    expect(screen.queryByRole("button", { name: /Play dispatch/ })).toBeNull();
    // The caveat names the model behind the figure (rule 6).
    expect(screen.getByText(/^Emulator estimate, lower bound/)).toBeInTheDocument();
  });

  it("says in the API's words why the plan did not load", async () => {
    loadPumpPlan.mockRejectedValue(
      new Error(`Run ${RUN} has no pump plan. Bake it with \`make bake\`.`),
    );
    render(<PumpsScreen />);
    // The region is replaced as the screen settles, so it is read again once it has.
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "Dispatch map" })).getByText(
          "The pump plan did not load",
        ),
      ).toBeInTheDocument(),
    );
    const map = screen.getByRole("region", { name: "Dispatch map" });
    expect(within(map).getByText(/has no pump plan\. Bake it with `make bake`\./)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Optimise" })).toBeNull();
    expect(screen.queryByText(/Something went wrong/i)).toBeNull();
  });

  it("offers the busiest cycle when this one sends no pump", async () => {
    loadPumpPlan.mockResolvedValue({ ...PLAN, assignments: [], totalMinutesSaved: 0 });
    loadPumpCycles.mockResolvedValue({
      busiestRunId: "MUM-20190702T0310Z-b",
      cycles: [
        {
          runId: RUN,
          cycleTs: "2019-07-02T07:10:00+05:30",
          nAssigned: 0,
          nUnassigned: 0,
          minutesSaved: 0,
        },
        {
          runId: "MUM-20190702T0310Z-b",
          cycleTs: "2019-07-02T08:40:00+05:30",
          nAssigned: 12,
          nUnassigned: 45,
          minutesSaved: 915,
        },
      ],
    });
    render(<PumpsScreen />);
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "Dispatch map" })).getByRole("button", {
          name: "Open the 08:40 plan",
        }),
      ).toBeInTheDocument(),
    );
    const map = screen.getByRole("region", { name: "Dispatch map" });
    expect(within(map).getByText("This cycle sends no pump")).toBeInTheDocument();
    expect(within(map).getByText(/Nothing on the 07:10 cycle crosses 45 cm/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Optimise" })).toBeNull();
  });

  it("opens on the cycle whose plan avoids the most, and says so", async () => {
    loadPumpCycles.mockResolvedValue(CYCLES);
    loadPumpPlan.mockImplementation((runId) => Promise.resolve({ ...PLAN, runId: runId ?? RUN }));
    render(<PumpsScreen />);

    await screen.findByRole("button", { name: "Optimise" });
    // Asked for the busiest cycle by name; never the newest the API would pick on its own.
    expect(loadPumpPlan).toHaveBeenCalledTimes(1);
    expect(loadPumpPlan).toHaveBeenCalledWith(BUSIEST);
    expect(
      screen.getByText(
        "Opened on the 08:40 cycle: of the baked cycles, its plan avoids the most minutes above 45 cm (915 min, 12 pumps). Pick another cycle to compare.",
      ),
    ).toBeInTheDocument();
  });

  it("opens on the newest run when no cycle list can be read", async () => {
    loadPumpCycles.mockRejectedValue(new Error("offline"));
    loadPumpPlan.mockResolvedValue(PLAN);
    render(<PumpsScreen />);

    await screen.findByRole("button", { name: "Optimise" });
    expect(loadPumpPlan).toHaveBeenCalledWith(undefined);
    expect(screen.queryByText(/^Opened on/)).toBeNull();
  });

  it("says why there is no map when the API refuses it, and never keeps loading", async () => {
    loadPumpPlan.mockResolvedValue(PLAN);
    loadPumpMap.mockRejectedValue(
      new Error(
        "The API at http://localhost:8000 answers the pump plan but not its dispatch map, so it is older than this screen. Restart it, or redeploy it, to draw the roads.",
      ),
    );
    render(<PumpsScreen />);

    await waitFor(() =>
      expect(screen.getAllByText(/older than this screen/).length).toBeGreaterThanOrEqual(2),
    );
    // The map, the rail and the timeline each say it, with what still stands; none is a skeleton.
    for (const node of screen.getAllByText(/older than this screen/)) {
      expect(node).toHaveTextContent(/The plan's own figures still stand\.$/);
    }
    expect(screen.queryByText(/Drawing the fleet/)).toBeNull();
    expect(document.querySelector(".skeleton-shimmer")).toBeNull();
    // The plan's own numbers are still on screen.
    expect(screen.getByRole("region", { name: "What the plan buys" })).toHaveTextContent(
      /2 of 2 pumps assigned/,
    );
  });

  it("says an answer with no map in it is settled, not loading", async () => {
    loadPumpPlan.mockResolvedValue(PLAN);
    loadPumpMap.mockResolvedValue(null);
    render(<PumpsScreen />);

    await waitFor(() =>
      expect(
        screen.getAllByText(/The API returned no dispatch map for this cycle/).length,
      ).toBeGreaterThanOrEqual(2),
    );
    expect(document.querySelector(".skeleton-shimmer")).toBeNull();
  });
});
