import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ALWAYS_AFTER,
  ALWAYS_BEFORE,
  arriveMs,
  departMs,
  DispatchClock,
  dispatchSpanMs,
  FINISHED_MS,
  IDLE_MS,
  STAGGER_MS,
  TRAVEL_MS,
} from "@/components/pumps/dispatch-clock";
import {
  clockFrame,
  depotLabels,
  LORRY_SAMPLES,
  lorrySamples,
  placeLabels,
  pointAlong,
  pumpRouteLayers,
  stackLabels,
  vertexTimes,
} from "@/components/map/layers/pump-routes";
import { ArrivalTimeline } from "@/components/pumps/arrival-timeline";
import {
  assignmentSentence,
  depotPoints,
  dispatchOrder,
  routeLegs,
  unservedPlaces,
} from "@/components/pumps/model";
import { drainScale, PlaceGauges } from "@/components/pumps/place-gauges";
import { describeNothingSent } from "@/app/pumps/pumps-screen";
import { parsePumpMap, type PumpPlan, type RawPumpMap } from "@/lib/api/pumps";
import { DUR_MS } from "@/lib/motion";

import fixture from "./pump-map.fixture.json";

// NumberFlow draws into a shadow root; the value it is handed is what the gauges are about.
vi.mock("@number-flow/react", () => ({
  default: ({ value, suffix = "" }: { value: number; suffix?: string }) => (
    <span data-number-flow="">{`${value}${suffix}`}</span>
  ),
}));

const MAP = parsePumpMap(fixture as unknown as RawPumpMap);

describe("M33 timing: section 8's 1.2 s drive, 150 ms apart", () => {
  it("staggers departures and arrivals by the catalogue's numbers", () => {
    expect(TRAVEL_MS).toBe(DUR_MS.routeDrawOn);
    expect(TRAVEL_MS).toBe(1200);
    expect(STAGGER_MS).toBe(150);
    expect(departMs(0)).toBe(0);
    expect(departMs(3)).toBe(450);
    expect(arriveMs(3)).toBe(1650);
    expect(dispatchSpanMs(12)).toBe(11 * 150 + 1200);
    expect(dispatchSpanMs(0)).toBe(0);
  });

  it("times each road vertex from departure to arrival at constant speed", () => {
    const path: [number, number][] = [
      [72.84, 19.0],
      [72.84, 19.01],
      [72.86, 19.01],
    ];
    const times = vertexTimes(path, 2);
    expect(times[0]).toBe(departMs(2));
    expect(times[2]).toBeCloseTo(arriveMs(2), 6);
    // A third of the length is a third of the time.
    expect(times[1]).toBeCloseTo(departMs(2) + TRAVEL_MS / 3, 6);
    const half = pointAlong(path, 0.5);
    expect(half[0]).toBeCloseTo(72.845, 9);
    expect(half[1]).toBeCloseTo(19.01, 9);
  });

  it("shows exactly one lorry position at any instant of its drive, and none after arrival", () => {
    const [leg] = routeLegs(MAP);
    const samples = lorrySamples(leg);
    expect(samples).toHaveLength(LORRY_SAMPLES - 1);
    expect(samples[0].window[0]).toBe(ALWAYS_BEFORE);
    expect(samples.at(-1)?.window[1]).toBeCloseTo(arriveMs(leg.order), 6);
    for (const t of [-500, 0, 10, 333, 700, 1199]) {
      const shown = samples.filter((s) => t >= s.window[0] && t < s.window[1]);
      expect(shown).toHaveLength(1);
    }
    expect(
      samples.filter(
        (s) => arriveMs(leg.order) >= s.window[0] && arriveMs(leg.order) < s.window[1],
      ),
    ).toHaveLength(0);
    // The lorry starts at the depot and is on its road throughout.
    expect(samples[0].position).toEqual(leg.path[0]);
  });
});

describe("the dispatch clock", () => {
  it("starts on its first read, once, and tells the screen", async () => {
    const onStart = vi.fn();
    const clock = new DispatchClock({ onStart });
    expect(clock.startMs).toBeNull();
    expect(clock.elapsed(1000)).toBe(0);
    expect(clock.elapsed(1600)).toBe(600);
    expect(clock.start(5000)).toBe(1000);
    await Promise.resolve();
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(onStart).toHaveBeenCalledWith(1000);
    expect(clockFrame(clock, 2850, 1600)).toEqual({ elapsed: 600, running: true });
    expect(clockFrame(clock, 2850, 5000).running).toBe(false);
  });

  it("reads finished from the first frame under reduced motion, and never asks for a frame", () => {
    const clock = new DispatchClock({ reduced: true });
    expect(clock.elapsed(0)).toBe(FINISHED_MS);
    expect(clockFrame(clock, 2850, 0)).toEqual({ elapsed: FINISHED_MS, running: false });
    expect(clockFrame(null, 2850, 0).running).toBe(false);
  });

  it("builds every layer of the map with the clock, the pump rings windowed from arrival", () => {
    const legs = routeLegs(MAP);
    const layers = pumpRouteLayers({
      legs,
      depots: depotPoints(MAP),
      unserved: unservedPlaces(MAP),
      clock: new DispatchClock(),
    }) as { id: string; props: Record<string, unknown> }[];
    expect(layers.map((l) => l.id)).toEqual([
      "pumps-unserved",
      "pumps-road-casing",
      "pumps-road",
      "pumps-depot",
      "pumps-depot-label",
      "pumps-place-ring",
      "pumps-place-core-before",
      "pumps-place-core-after",
      "pumps-pump-here",
      "pumps-lorry",
      "pumps-place-label",
    ]);
    const here = layers.find((l) => l.id === "pumps-pump-here");
    const window = (here?.props.getDispatchWindow as (d: unknown) => [number, number])(legs[4]);
    expect(window).toEqual([arriveMs(4), ALWAYS_AFTER]);
    expect(here?.props.clockSpanMs).toBe(dispatchSpanMs(legs.length));
  });
});

describe("depot names", () => {
  it("stacks the names of depots too close to label apart, and keeps the rest", () => {
    const labels = depotLabels(depotPoints(MAP));
    const texts = labels.map((l) => l.text);
    // Kurla's BEST depot and the L ward office are 540 m apart: one label, two lines.
    expect(texts).toContain("Kurla Depot (BEST)\nBMC L Ward Office (Kurla)");
    // Every depot is named exactly once.
    const names = texts.flatMap((t) => t.split("\n")).sort();
    expect(names).toEqual(
      depotPoints(MAP)
        .map((d) => d.name)
        .sort(),
    );
    expect(labels.length).toBeLessThan(depotPoints(MAP).length);
  });
});

describe("place names on the map", () => {
  it("names every place a pump goes, at every zoom, stacking the two Danda roads 17 m apart", () => {
    const legs = routeLegs(MAP);
    const labels = placeLabels(legs);
    const texts = labels.map((l) => l.text);
    // P-10 and P-03 go to two streets 17 m apart: one label, two lines, never overprinted.
    expect(texts.some((t) => t.includes("Danda Avenue") && t.includes("Danda Boulevard"))).toBe(
      true,
    );
    const names = texts.flatMap((t) => t.split("\n")).sort();
    expect(names).toEqual(legs.map((l) => l.targetName).sort());
    // No name the street-names rule forbids, and none empty.
    for (const name of names) {
      expect(name).not.toMatch(/^unnamed/i);
      expect(name.trim()).not.toBe("");
    }
    const layer = (
      pumpRouteLayers({ legs, depots: depotPoints(MAP) }) as {
        id: string;
        props: Record<string, unknown>;
      }[]
    ).find((l) => l.id === "pumps-place-label");
    // Drawn by this layer rather than the shared hotspot labels, which start at zoom 11.5.
    expect(layer?.props.data).toEqual(labels);
    expect(String(layer?.props.fontFamily)).not.toContain("var(");
  });

  it("puts a name on the side of its ring where it covers no other place", () => {
    const byName = new Map(placeLabels(routeLegs(MAP)).map((l) => [l.text.split("\n")[0], l]));
    // sangharsh nagar road's ring is 1.2 km east of Lokmanya Tilak Nagar's, on the same line.
    expect(byName.get("Lokmanya Tilak Nagar")?.anchor).toBe("end");
    expect(byName.get("sangharsh nagar road")?.anchor).toBe("start");
    // Road 13's ring is 1.5 km east of Jijamata Road's.
    expect(byName.get("Jijamata Road")?.anchor).toBe("end");
    expect(byName.get("Road 13")?.anchor).toBe("start");
    // A place with nothing beside it reads to the right.
    expect(byName.get("Gurunanak Marg")?.anchor).toBe("start");
  });

  it("names a depot only for the pump being pointed at", () => {
    const legs = routeLegs(MAP);
    const depotLabelData = (selectedPumpId: string | null) =>
      (
        pumpRouteLayers({ legs, depots: depotPoints(MAP), selectedPumpId }) as {
          id: string;
          props: Record<string, unknown>;
        }[]
      ).find((l) => l.id === "pumps-depot-label")?.props.data as { text: string }[];
    expect(depotLabelData(null)).toEqual([]);
    expect(depotLabelData("P-08").map((d) => d.text)).toEqual(["Kurla Depot (BEST)"]);
  });

  it("skips a place with no name rather than printing an empty label", () => {
    const [leg] = routeLegs(MAP);
    expect(placeLabels([{ ...leg, targetName: "" }])).toEqual([]);
    expect(stackLabels([{ name: "Hindmata", position: [72.84, 19.01] }], 700)).toEqual([
      { text: "Hindmata", position: [72.84, 19.01] },
    ]);
  });
});

describe("before Optimise: the fleet waits at its depots", () => {
  it("never starts, draws no road, and holds every lorry at its depot", async () => {
    const onStart = vi.fn();
    const clock = new DispatchClock({ idle: true, onStart });
    expect(clock.elapsed(1000)).toBe(IDLE_MS);
    expect(clock.start(1000)).toBeNaN();
    expect(clock.startMs).toBeNull();
    await Promise.resolve();
    expect(onStart).not.toHaveBeenCalled();
    // Idle under reduced motion too: waiting is a state, not a motion.
    expect(new DispatchClock({ idle: true, reduced: true }).elapsed(0)).toBe(IDLE_MS);
    expect(clockFrame(clock, 2850, 1000)).toEqual({ elapsed: IDLE_MS, running: false });

    const legs = routeLegs(MAP);
    for (const leg of legs) {
      // Every vertex of every road is reached after the idle reading: none is drawn.
      expect(Math.min(...vertexTimes(leg.path, leg.order))).toBeGreaterThan(IDLE_MS);
      // Exactly one lorry position is shown, and it is the depot.
      const shown = lorrySamples(leg).filter(
        (s) => IDLE_MS >= s.window[0] && IDLE_MS < s.window[1],
      );
      expect(shown).toHaveLength(1);
      expect(shown[0].position).toEqual(leg.path[0]);
    }
  });

  it("keeps every gauge full and the timeline at the no-pump picture, under reduced motion too", () => {
    const idle = { key: "idle", startMs: null, idle: true };
    render(
      <PlaceGauges
        legs={MAP.legs}
        thresholdCm={45}
        order={dispatchOrder(MAP)}
        clock={idle}
        reduced
      />,
    );
    const first = within(screen.getByRole("list", { name: "Places the pumps go" })).getAllByRole(
      "button",
    )[0];
    expect(first.querySelector("[data-number-flow]")).toHaveTextContent("115 min");
    expect(first.querySelector<HTMLElement>("[data-testid=gauge-fill]")?.style.transform).toBe(
      "scaleY(1)",
    );

    render(
      <ArrivalTimeline
        legs={MAP.legs}
        cycleTs={MAP.cycleTs}
        stepMin={MAP.stepMin}
        nSteps={MAP.nSteps}
        thresholdCm={45}
        order={dispatchOrder(MAP)}
        clock={idle}
        reduced
      />,
    );
    expect(screen.queryAllByTestId("arrival-marker")).toHaveLength(0);
    expect(screen.queryAllByTestId("cell-after")).toHaveLength(0);
    expect(screen.getAllByTestId("cell-before").length).toBeGreaterThan(0);
  });
});

describe("M34: the gauges drain as each pump arrives", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("holds the no-pump minutes until the pump arrives, then rolls to the plan's", () => {
    const order = dispatchOrder(MAP);
    const start = performance.now();
    render(
      <PlaceGauges
        legs={MAP.legs}
        thresholdCm={45}
        order={order}
        clock={{ key: "a", startMs: start }}
      />,
    );
    const first = within(screen.getByRole("list", { name: "Places the pumps go" })).getAllByRole(
      "button",
    )[0];
    expect(first).toHaveTextContent("Lokmanya Tilak Nagar");
    expect(first).toHaveTextContent("115 min");
    const index = order.get("P-08") ?? 0;
    act(() => {
      vi.advanceTimersByTime(arriveMs(index) + 1);
    });
    expect(first.querySelector("[data-number-flow]")).toHaveTextContent("15 min");
    const fill = first.querySelector<HTMLElement>("[data-testid=gauge-fill]");
    expect(fill?.style.transitionProperty).toBe("transform, background-color");
    expect(fill?.style.transitionDuration).toBe(`${DUR_MS.gaugeDrain}ms`);
    expect(fill?.style.transform).toMatch(/^scaleY\(0\.\d+\)$/);
  });

  it("is at the plan's value with no drain under reduced motion", () => {
    render(
      <PlaceGauges
        legs={MAP.legs}
        thresholdCm={45}
        order={dispatchOrder(MAP)}
        clock={{ key: "a", startMs: null }}
        reduced
      />,
    );
    const first = within(screen.getByRole("list", { name: "Places the pumps go" })).getAllByRole(
      "button",
    )[0];
    expect(first.querySelector("[data-number-flow]")).toHaveTextContent("15 min");
    expect(
      first.querySelector<HTMLElement>("[data-testid=gauge-fill]")?.style.transitionProperty,
    ).toBe("none");
    expect(drainScale(50, 25)).toBe(0.5);
    expect(drainScale(null, 25)).toBe(1);
  });
});

describe("M35: arrival markers slide in when the dispatch starts", () => {
  const props = {
    legs: MAP.legs,
    cycleTs: MAP.cycleTs,
    stepMin: MAP.stepMin,
    nSteps: MAP.nSteps,
    thresholdCm: 45,
  };

  it("waits for the map to start the dispatch, then draws one marker per pump", () => {
    const { rerender } = render(<ArrivalTimeline {...props} clock={{ key: "a", startMs: null }} />);
    expect(screen.queryAllByTestId("arrival-marker")).toHaveLength(0);
    rerender(<ArrivalTimeline {...props} clock={{ key: "a", startMs: 100 }} />);
    expect(screen.getAllByTestId("arrival-marker")).toHaveLength(12);
  });

  it("draws the markers where they belong under reduced motion", () => {
    render(<ArrivalTimeline {...props} clock={{ key: "a", startMs: null }} reduced />);
    expect(screen.getAllByTestId("arrival-marker")).toHaveLength(12);
    expect(screen.getAllByTestId("cell-after").length).toBeGreaterThan(0);
  });

  it("draws what stays above 45 cm with a pump only once that pump has arrived", () => {
    vi.useFakeTimers();
    const order = dispatchOrder(MAP);
    const start = performance.now();
    render(<ArrivalTimeline {...props} order={order} clock={{ key: "a", startMs: start }} />);
    expect(screen.queryAllByTestId("cell-after")).toHaveLength(0);
    act(() => {
      vi.advanceTimersByTime(dispatchSpanMs(MAP.legs.length) + 1);
    });
    expect(screen.getAllByTestId("cell-after").length).toBeGreaterThan(0);
    vi.useRealTimers();
  });
});

describe("honest states and the text alternative", () => {
  it("reads every assignment as a sentence", () => {
    const leg = MAP.legs.find((l) => l.pumpId === "P-08");
    expect(leg).toBeDefined();
    const line = assignmentSentence(leg!, 45);
    expect(line).toMatch(
      /^P-08 leaves Kurla Depot \(BEST\) for Lokmanya Tilak Nagar, 7 min by road over 3\.5 km;/,
    );
    expect(line).toMatch(/115 min above 45 cm with no pump, 15 min with it\.$/);
  });

  it("names the cycles that send pumps and offers the busiest", () => {
    const quiet: PumpPlan = {
      runId: "MUM-20190702T0140Z-sky1.0-twin1.0-flash0.1-baked",
      thresholdCm: 45,
      benefitLabel: "",
      inventory: "synthetic",
      pumps: [],
      assignments: [],
      unassigned: [],
      totalMinutesSaved: 0,
    };
    const said = describeNothingSent(quiet, {
      busiestRunId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
      cycles: [
        {
          runId: "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
          cycleTs: "2019-07-02T06:40:00+05:30",
          nAssigned: 4,
          nUnassigned: 0,
          minutesSaved: 90,
        },
        {
          runId: quiet.runId,
          cycleTs: "2019-07-02T07:10:00+05:30",
          nAssigned: 0,
          nUnassigned: 0,
          minutesSaved: 0,
        },
        {
          runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
          cycleTs: "2019-07-02T08:40:00+05:30",
          nAssigned: 12,
          nUnassigned: 45,
          minutesSaved: 915,
        },
      ],
    });
    expect(said.title).toBe("This cycle sends no pump");
    expect(said.description).toMatch(/^Nothing on the 07:10 cycle crosses 45 cm/);
    expect(said.description).toMatch(/06:40 \(4\), 08:40 \(12\)\.$/);
    expect(said.offer).toEqual({
      runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
      clock: "08:40",
    });
    expect(describeNothingSent(null, null).description).toBe(
      "There is no pump plan for this cycle.",
    );
  });
});
