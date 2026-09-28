import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  citizenReport,
  fetchByPath,
  seedReport,
} from "@/components/citizen/__tests__/report-fixtures";
import type { CitizenMapProps } from "@/components/citizen/citizen-map";
import { MY_REPORTS_KEY } from "@/components/citizen/my-reports";
import { SYNTHETIC_LABEL } from "@/components/citizen/report-card";
import type { CitizenRun } from "@/lib/maps/citizen-run";

import {
  DashboardScreen,
  HONESTY_CHIPS,
  NEARBY_RADIUS_M,
  UNLISTED_ROAD,
  distanceM,
  nearbyStreets,
  runLine,
} from "./dashboard-screen";

/** The run the mocked map "loads", or null for a map that never finishes. */
const fake = vi.hoisted(() => ({ run: null as unknown }));

// The map is exercised by `components/citizen/citizen-map.test.tsx`; here it would only ask for a
// WebGL context jsdom does not have. The stand-in reports what the screen handed it, and loads the
// run it was asked for the way the real map does, through `onRunLoaded`.
vi.mock("@/components/citizen/citizen-map", async () => {
  const actual = await vi.importActual<typeof import("@/components/citizen/citizen-map")>(
    "@/components/citizen/citizen-map",
  );
  const { useEffect } = await import("react");
  function CitizenMap(props: CitizenMapProps) {
    const { onRunLoaded, runId } = props;
    useEffect(() => {
      const run = fake.run as CitizenRun | null;
      if (!run) return;
      onRunLoaded?.({ ...run, provenance: { ...run.provenance, runId: runId ?? "newest" } });
    }, [onRunLoaded, runId]);
    return (
      <div
        data-testid="citizen-map"
        data-run-id={runId ?? ""}
        data-step={String(props.step ?? 0)}
        data-reports={(props.reports ?? []).map((r) => r.id).join(",")}
        data-selected={props.selectedReportId ?? ""}
        data-focus={props.focus ? `${props.focus.lat},${props.focus.lon},${props.focus.zoom}` : ""}
      />
    );
  }
  return { ...actual, CitizenMap };
});

const HINDMATA = { lon: 72.841, lat: 19.012 };

/** Two 5-minute steps at 06:45 and 06:50 IST, as a baked cycle writes them. */
const VALID_TS = ["2019-07-02T06:45:00+05:30", "2019-07-02T06:50:00+05:30"];

const segments = [
  {
    id: "S-near-named",
    name: "Dr Ambedkar Road",
    path: [[72.8415, 19.0125]] as [number, number][],
    depthCm: [10, 40],
  },
  {
    id: "S-near-unnamed",
    displayName: "off Dr Ambedkar Road",
    path: [[72.8412, 19.0121]] as [number, number][],
    depthCm: [35, 50],
  },
  {
    id: "S-near-dry",
    name: "Tulsi Pipe Road",
    path: [[72.8413, 19.0122]] as [number, number][],
    depthCm: [2, 3],
  },
  {
    // About 8 km north: inside the AOI, not inside the radius.
    id: "S-far",
    name: "Milan Subway",
    path: [[72.84, 19.079]] as [number, number][],
    depthCm: [80, 90],
  },
];

describe("distanceM", () => {
  it("measures a tenth of a degree of latitude as about 11 km", () => {
    expect(distanceM({ lon: 72.84, lat: 19.0 }, [72.84, 19.1])).toBeCloseTo(11_132, -2);
  });

  it("shrinks a degree of longitude by the cosine of the latitude", () => {
    const atEquator = distanceM({ lon: 0, lat: 0 }, [1, 0]);
    const atMumbai = distanceM({ lon: 72.84, lat: 19.0 }, [73.84, 19.0]);
    expect(atMumbai).toBeLessThan(atEquator);
    expect(atMumbai / atEquator).toBeCloseTo(Math.cos((19 * Math.PI) / 180), 3);
  });
});

describe("nearbyStreets", () => {
  it("keeps only streets within the radius of the reader", () => {
    const rows = nearbyStreets(segments, VALID_TS, 30, HINDMATA);
    expect(rows.map((r) => r.id)).toEqual(["S-near-unnamed", "S-near-named"]);
    expect(distanceM(HINDMATA, segments[3].path[0])).toBeGreaterThan(NEARBY_RADIUS_M);
  });

  it("falls back to the whole city when there is no position", () => {
    const rows = nearbyStreets(segments, VALID_TS, 30, null);
    expect(rows.map((r) => r.id)).toEqual(["S-far", "S-near-unnamed", "S-near-named"]);
  });

  it("prints the display name where OSM has none, and never 'Unnamed road'", () => {
    const rows = nearbyStreets(segments, VALID_TS, 30, HINDMATA);
    expect(rows.find((r) => r.id === "S-near-unnamed")?.name).toBe("off Dr Ambedkar Road");
    expect(rows.find((r) => r.id === "S-near-named")?.name).toBe("Dr Ambedkar Road");
    const bare = nearbyStreets(
      [{ id: "S-bare", path: [[72.8412, 19.0121]], depthCm: [35, 50] }],
      VALID_TS,
      30,
      HINDMATA,
    );
    expect(bare[0]?.name).toBe(UNLISTED_ROAD);
  });

  it("gives the last passable step, or none when the street is already over the vehicle", () => {
    const rows = nearbyStreets(segments, VALID_TS, 30, HINDMATA);
    // 10 cm then 40 cm: passable at 06:45, over a car by 06:50.
    expect(rows.find((r) => r.id === "S-near-named")?.passableUntil).toBe("06:45");
    // 35 cm at the first step: already impassable.
    expect(rows.find((r) => r.id === "S-near-unnamed")?.passableUntil).toBeNull();
  });

  it("changes with the vehicle, because the threshold is the vehicle's", () => {
    // A bus stops at 45 cm. At 06:50 the 40 cm street is caution for it (over half of 45 cm) and
    // never stops it, so it stays passable to the end of the run - where a car's 30 cm cut it
    // off at 06:45.
    const forBus = nearbyStreets(segments, VALID_TS, 45, HINDMATA, 1);
    const named = forBus.find((r) => r.id === "S-near-named");
    expect(named?.passableUntil).toBe("06:50");
    expect(named?.throughEnd).toBe(true);
    // 50 cm crosses 45 cm at the second step, so at the first the bus has until 06:45.
    expect(
      nearbyStreets(segments, VALID_TS, 45, HINDMATA).find((r) => r.id === "S-near-unnamed")
        ?.passableUntil,
    ).toBe("06:45");
  });

  it("leaves out a street the map paints passable and that never stops this vehicle", () => {
    // 10 cm at 06:45 is under half a bus's 45 cm, and 40 cm never reaches it: blue on the map
    // at the first step, and nothing to list.
    expect(nearbyStreets(segments, VALID_TS, 45, HINDMATA, 0).map((r) => r.id)).not.toContain(
      "S-near-named",
    );
  });

  it("agrees with the map at the chosen step: the depth then, and until when from then", () => {
    const atFirst = nearbyStreets(segments, VALID_TS, 30, HINDMATA, 0);
    const atSecond = nearbyStreets(segments, VALID_TS, 30, HINDMATA, 1);
    // 06:45: 10 cm and still passable for a car, closing at the next step.
    expect(atFirst.find((r) => r.id === "S-near-named")).toMatchObject({
      cm: 10,
      passableUntil: "06:45",
      throughEnd: false,
    });
    // 06:50: 40 cm and impassable, which is what the street's colour says at that step.
    expect(atSecond.find((r) => r.id === "S-near-named")).toMatchObject({
      cm: 40,
      passableUntil: null,
    });
  });

  it("lists impassable streets first, then caution, then the ones that close soonest", () => {
    const ranked = nearbyStreets(
      [
        { id: "closes-late", path: [[72.8415, 19.0125]], depthCm: [0, 0, 5, 35] },
        { id: "caution", path: [[72.8415, 19.0125]], depthCm: [20, 20, 20, 20] },
        { id: "closes-soon", path: [[72.8415, 19.0125]], depthCm: [0, 35, 35, 35] },
        { id: "over", path: [[72.8415, 19.0125]], depthCm: [31, 31, 31, 31] },
      ],
      [...VALID_TS, "2019-07-02T06:55:00+05:30", "2019-07-02T07:00:00+05:30"],
      30,
      HINDMATA,
      0,
    );
    expect(ranked.map((r) => r.id)).toEqual(["over", "caution", "closes-soon", "closes-late"]);
  });

  it("lists a street a vehicle must think about, at half its stopping depth", () => {
    // The 2-3 cm street is below half of a car's 30 cm and is not worth a row.
    expect(nearbyStreets(segments, VALID_TS, 30, HINDMATA).map((r) => r.id)).not.toContain(
      "S-near-dry",
    );
  });
});

function run(cycleTs: string | null): CitizenRun {
  return {
    provenance: {
      runId: "MUM-20190702T0640Z-sky1.0-twin1.0-flash0.3-baked",
      cycleTs,
      mode: "replay",
      bundle: "MUM-2019-07-02",
      nSteps: 36,
      ensembleN: 20,
    },
    bounds: [72.815, 18.995, 72.905, 19.135],
    segments: [],
    baseSegments: [],
    depthCm: new Map(),
    validTs: VALID_TS,
  };
}

describe("runLine", () => {
  it("names the run and the time it is for", () => {
    const line = runLine(run("2019-07-02T06:40:00+05:30"), false);
    expect(line).toContain("06:40 IST");
    expect(line).toContain("MUM-");
  });

  it("says the load failed rather than claiming a run is still coming", () => {
    expect(runLine(null, true)).toMatch(/did not load/);
    expect(runLine(null, false)).toMatch(/Loading/);
  });

  it("does not invent a time for a run that carries none", () => {
    expect(runLine(run(null), true)).toMatch(/did not load/);
  });
});

describe("DashboardScreen", () => {
  it("renders the honest empty rail before a trip is planned", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 503 })),
    );
    render(<DashboardScreen />);

    expect(await screen.findAllByText("No trip yet")).not.toHaveLength(0);
    expect(
      screen.getAllByText(/Choose where you are and where you are going/).length,
    ).toBeGreaterThan(0);
    // No route, so no ETA is claimed anywhere on the screen.
    expect(screen.queryByText(/Shortest way/)).toBeNull();
    vi.unstubAllGlobals();
  });

  it("carries the three honesty chips verbatim", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 503 })),
    );
    render(<DashboardScreen />);
    for (const chip of HONESTY_CHIPS) {
      expect(await screen.findByText(chip)).toBeInTheDocument();
    }
    vi.unstubAllGlobals();
  });

  /**
   * The entry used to sit inside an opaque `bg-ink absolute inset-0 z-30` div that carried
   * `hidden={introDone}`. That div had to be hidden, or it covered the map for ever; being hidden
   * the moment `onDone` fired cut M27's 900 ms cross-fade to nothing, and together with the
   * hydration defect fixed in `globe-entry.tsx` it hid the globe for its whole four seconds. The
   * overlay is its own `fixed` layer and removes itself, so it needs nothing around it.
   */
  it("mounts the entry with no hidden wrapper over it", () => {
    window.sessionStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 503 })),
    );
    render(<DashboardScreen />);

    const overlay = document.querySelector('[data-slot="dashboard-intro"]');
    expect(overlay).not.toBeNull();
    for (let node = overlay?.parentElement; node; node = node.parentElement) {
      expect(node.hasAttribute("hidden")).toBe(false);
      expect(node.getAttribute("aria-hidden")).not.toBe("true");
    }
    // The pane the entry hands over to is mounted and still waiting for it.
    expect(
      document.querySelector('[data-slot="dashboard-stage"]')?.getAttribute("data-handover"),
    ).toBe("playing");
    vi.unstubAllGlobals();
  });
});

// ---- The screen with a run, reports and a cycle registry ------------------------------------

const CYCLE_0840 = "2019-07-02T08:40:00+05:30";

/** 36 five-minute steps from 08:45 to 11:40 IST. */
const STEPS_0840 = Array.from({ length: 36 }, (_, i) =>
  new Date(Date.parse(CYCLE_0840) + (i + 1) * 5 * 60_000).toISOString(),
);

/** The 08:40 cycle as the map loads it: one street by Hindmata, dry until 09:35, 40 cm after. */
function run0840(): CitizenRun {
  const depth = STEPS_0840.map((_, i) => (i < 11 ? 0 : 40));
  return {
    provenance: {
      runId: "set-by-the-map",
      cycleTs: CYCLE_0840,
      mode: "baked",
      bundle: "MUM-2019-07-02",
      nSteps: 36,
      ensembleN: 50,
    },
    bounds: [72.815, 18.995, 72.905, 19.135],
    segments: [
      {
        id: "S-hindmata",
        name: "Dr Ambedkar Road",
        path: [[72.8415, 19.0125]],
        depthCm: depth,
      } as unknown as CitizenRun["segments"][number],
    ],
    baseSegments: [],
    depthCm: new Map([["S-hindmata", depth]]),
    validTs: STEPS_0840,
  };
}

const RUNS = {
  runs: [
    {
      run_id: "MUM-20190702T0340Z-baked",
      cycle_ts: "2019-07-02T09:10:00+05:30",
      created_at: "2026-09-26T02:17:18+05:30",
    },
    {
      run_id: "MUM-20190702T0310Z-baked",
      cycle_ts: CYCLE_0840,
      created_at: "2026-09-26T02:10:00+05:30",
    },
  ],
};

function mapNode(): HTMLElement {
  return screen.getByTestId("citizen-map");
}

/** The street list under its heading, whichever shape the rail is in. */
function streetList(name: string): HTMLElement {
  return screen.getByRole("heading", { name }).closest("section") as HTMLElement;
}

describe("DashboardScreen with a run", () => {
  afterEach(() => {
    fake.run = null;
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("opens on the 08:40 storm cycle at +60 min, and the street list agrees with the map", async () => {
    fake.run = run0840();
    vi.stubGlobal("fetch", fetchByPath([[(path) => path === "/v1/runs", () => RUNS]]));
    render(<DashboardScreen />);

    // Not the newest run (09:10, after the storm): the one the registry has at 08:40.
    expect((await screen.findByTestId("citizen-map")).dataset.runId).toBe(
      "MUM-20190702T0310Z-baked",
    );
    // +60 min is step 11, and the words say which step it is.
    expect(await screen.findByText("2 July 2019 replay: streets at 09:40 (+60 min)")).toBeTruthy();
    expect(mapNode().dataset.step).toBe("11");
    const list = streetList("The worst streets in the city at 09:40 (+60 min)");
    expect(within(list).getByText("Impassable at 09:40")).toBeTruthy();
    expect(within(list).getByText("40 cm")).toBeTruthy();

    // "Now": the map and the list move together to the first step, where the street is dry.
    fireEvent.click(screen.getByRole("button", { name: "Now, 08:45 (+5 min)" }));
    expect(mapNode().dataset.step).toBe("0");
    const now = streetList("The worst streets in the city at 08:45 (+5 min)");
    expect(within(now).getByText("Passable until 09:35")).toBeTruthy();
    expect(within(now).getByText("0 cm")).toBeTruthy();
  });

  it("switches cycle from the select, and the map loads that run", async () => {
    fake.run = run0840();
    vi.stubGlobal("fetch", fetchByPath([[(path) => path === "/v1/runs", () => RUNS]]));
    render(<DashboardScreen />);
    const select = (await screen.findByRole("combobox", {
      name: "Forecast cycle, 2 Jul 2019",
    })) as HTMLSelectElement;
    expect(select.value).toBe("MUM-20190702T0310Z-baked");
    expect(within(select).getByRole("option", { name: "Forecast from 09:10 IST" })).toBeTruthy();

    fireEvent.change(select, { target: { value: "MUM-20190702T0340Z-baked" } });
    expect(mapNode().dataset.runId).toBe("MUM-20190702T0340Z-baked");
  });

  it("says why the map does not open at now", async () => {
    fake.run = run0840();
    vi.stubGlobal("fetch", fetchByPath([[(path) => path === "/v1/runs", () => RUNS]]));
    render(<DashboardScreen />);
    expect(await screen.findByText(/The map opens at \+60 min/)).toBeTruthy();
  });

  it("keeps the two clocks apart: today's outlook never recolours the replay map", async () => {
    fake.run = run0840();
    vi.stubGlobal("fetch", fetchByPath([[(path) => path === "/v1/runs", () => RUNS]]));
    render(<DashboardScreen />);
    await screen.findByText("2 July 2019 replay: streets at 09:40 (+60 min)");
    const before = { runId: mapNode().dataset.runId, step: mapNode().dataset.step };

    fireEvent.click(screen.getByRole("button", { name: "Today, next 3 h" }));
    expect(await screen.findByTestId("live-outlook-card")).toBeTruthy();
    expect(screen.getByText(/The map stays on the 2 July 2019 replay\./)).toBeTruthy();
    // Same run, same step, and the strip over the map still names its clock.
    expect(mapNode().dataset.runId).toBe(before.runId);
    expect(mapNode().dataset.step).toBe(before.step);
    expect(screen.getByText("2 July 2019 replay: streets at 09:40 (+60 min)")).toBeTruthy();
  });

  it("draws the complaints as pins, leaves dismissed ones off, and opens a pin's card", async () => {
    fake.run = run0840();
    vi.stubGlobal(
      "fetch",
      fetchByPath([
        [(path) => path === "/v1/runs", () => RUNS],
        [
          (path) => path === "/v1/reports",
          () => ({
            count: 3,
            reports: [
              seedReport(),
              citizenReport(),
              citizenReport({ id: "rpt-gone", status: "dismissed" }),
            ],
          }),
        ],
      ]),
    );
    render(<DashboardScreen />);
    expect(await screen.findByText(SYNTHETIC_LABEL)).toBeTruthy();
    expect(mapNode().dataset.reports).toBe("rpt-1789225538684,seed-RPT-MUM-HS-01-ankle");

    expect(mapNode().dataset.focus).toBe("");
    fireEvent.click(screen.getAllByRole("button", { name: "Show on the map" })[0]!);
    expect(mapNode().dataset.selected).toBe("rpt-1789225538684");
    expect(document.querySelector('[data-slot="selected-report"]')).not.toBeNull();
    // The map is asked to bring that report into view, at the desk's zoom.
    expect(mapNode().dataset.focus).toMatch(/^19\.\d+,72\.\d+,15$/);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.querySelector('[data-slot="selected-report"]')).toBeNull();
    expect(mapNode().dataset.selected).toBe("");
  });

  it("puts the phone's sheet down when a complaint is shown on the map, and focuses its card", async () => {
    fake.run = run0840();
    vi.stubGlobal(
      "fetch",
      fetchByPath([
        [(path) => path === "/v1/runs", () => RUNS],
        [(path) => path === "/v1/reports", () => ({ count: 1, reports: [seedReport()] })],
      ]),
    );
    render(<DashboardScreen />);
    await screen.findByText(SYNTHETIC_LABEL);

    // The reader has pulled the sheet all the way up to read the list.
    const handle = () => screen.getByRole("button", { name: /^Your way there, / });
    fireEvent.click(handle());
    fireEvent.click(handle());
    expect(handle().getAttribute("aria-label")).toMatch(/fully open/);

    fireEvent.click(screen.getByRole("button", { name: "Show on the map" }));
    expect(mapNode().dataset.selected).toBe("seed-RPT-MUM-HS-01-ankle");
    // Down, so the map, the ringed pin and the card are what the reader sees.
    expect(handle().getAttribute("aria-label")).toMatch(/collapsed/);
    const card = document.querySelector('[data-slot="selected-report"]');
    expect(card).not.toBeNull();
    expect(document.activeElement).toBe(card);
  });

  it("shows the reader's own report with the status and note the ward desk gave it", async () => {
    window.localStorage.setItem(
      MY_REPORTS_KEY,
      JSON.stringify([{ id: "rpt-1789225538684", sentAt: "2026-09-27T12:40:02.000Z" }]),
    );
    vi.stubGlobal(
      "fetch",
      fetchByPath([
        [
          (path) => path === "/v1/reports/rpt-1789225538684",
          () =>
            citizenReport({
              status: "seen",
              history: [
                { status: "received", ts: "2026-09-27T18:10:00+05:30", role: "citizen" },
                {
                  status: "seen",
                  ts: "2026-09-27T18:20:00+05:30",
                  role: "ward officer",
                  note: "Logged for the F/North ward crew",
                },
              ],
            }),
        ],
      ]),
    );
    render(<DashboardScreen />);
    const section = (await screen.findByRole("heading", { name: "My reports" })).closest(
      "section",
    ) as HTMLElement;
    expect(await within(section).findByText("Seen by the ward desk")).toBeTruthy();
    expect(within(section).getByText(/Logged for the F\/North ward crew/)).toBeTruthy();
  });

  it("tells a reader with no reports how to raise one", async () => {
    vi.stubGlobal("fetch", fetchByPath([]));
    render(<DashboardScreen />);
    expect(await screen.findByText("You have not sent a report from this browser.")).toBeTruthy();
  });
});
