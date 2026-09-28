import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ArrivalTimeline, cellRuns, raceSentence } from "@/components/pumps/arrival-timeline";
import { depthCaption } from "@/components/varuna/pump-board";
import {
  byFloodStart,
  clockAt,
  dispatchBounds,
  dispatchOrder,
  race,
  routeLegs,
  unservedPlaces,
} from "@/components/pumps/model";
import { gaugePercent, gaugeScaleCm, PlaceGauges } from "@/components/pumps/place-gauges";
import { arriveMs } from "@/components/pumps/dispatch-clock";
import { benefitCaveat, plainMinutes, PumpHeadline } from "@/components/pumps/pump-headline";
import { loadPumpPlan, parsePumpMap, type PumpPlan, type RawPumpMap } from "@/lib/api/pumps";

// `GET /v1/pumps/map` for the 08:40 demo cycle (MUM-20190702T0310Z), as the API served it on
// 2026-09-28, with each road cut to six vertices. Every number below is the API's.
import fixture from "./pump-map.fixture.json";

// NumberFlow draws into a shadow root; the value it is handed is what the headline is about.
vi.mock("@number-flow/react", () => ({
  default: ({ value, suffix = "" }: { value: number; suffix?: string }) => (
    <span data-number-flow="">{`${value}${suffix}`}</span>
  ),
}));

const MAP = parsePumpMap(fixture as unknown as RawPumpMap);

/** The plan `GET /v1/pumps` serves for the same cycle, rebuilt from the map's own legs. */
const PLAN: PumpPlan = {
  runId: MAP.runId,
  thresholdCm: MAP.thresholdCm,
  benefitLabel: MAP.benefitLabel,
  inventory: MAP.inventory,
  pumps: MAP.legs.map((l) => ({
    id: l.pumpId,
    capacityM3PerHour: l.capacityM3PerHour,
    depot: l.depot.name,
    status: "available",
  })),
  assignments: MAP.legs.map((l) => ({
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
  unassigned: MAP.unassigned.map((u) => ({ name: u.name, minutesAbove: u.minutesAbove })),
  totalMinutesSaved: MAP.summary.minutesSaved,
};

describe("parsePumpMap", () => {
  it("keeps the plan's figures and every leg's two series", () => {
    expect(MAP.legs).toHaveLength(12);
    expect(MAP.summary.pumpsDispatched).toBe(12);
    expect(MAP.summary.minutesSaved).toBe(915);
    expect(MAP.benefitModel).toBe("emulator");
    expect(MAP.inventory).toBe("synthetic");
    for (const leg of MAP.legs) {
      expect(leg.depthBeforeCm).toHaveLength(MAP.nSteps);
      expect(leg.depthAfterCm).toHaveLength(MAP.nSteps);
      expect(leg.depot.lon).not.toBeNull();
      expect(leg.target.name).not.toMatch(/^unnamed/i);
    }
  });

  it("tolerates an empty body without inventing a number", () => {
    const empty = parsePumpMap({});
    expect(empty.legs).toEqual([]);
    expect(empty.summary.minutesSaved).toBe(0);
    expect(empty.summary.helpedMost).toBeNull();
    expect(empty.emulator).toBeNull();
  });
});

describe("loading the plan", () => {
  it("says the API did not answer and how to start it, not the browser's words", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    try {
      await expect(loadPumpPlan("MUM-x")).rejects.toThrow(
        /^The VARUNA API did not answer at .+, so there is no pump plan to show\. .*make dev starts it\.$/,
      );
      // A request the screen cancelled is not an outage, and is not reported as one.
      const controller = new AbortController();
      controller.abort();
      await expect(loadPumpPlan("MUM-x", controller.signal)).rejects.toThrow("Failed to fetch");
    } finally {
      globalThis.fetch = original;
    }
  });
});

describe("dispatch model", () => {
  it("frames every depot and every place the plan sends a pump to", () => {
    const bounds = dispatchBounds(MAP);
    expect(bounds).toBeDefined();
    const [[w, s], [e, n]] = bounds!;
    for (const leg of MAP.legs) {
      for (const [lon, lat] of [
        [leg.depot.lon!, leg.depot.lat!],
        [leg.target.lon!, leg.target.lat!],
      ]) {
        expect(lon).toBeGreaterThan(w);
        expect(lon).toBeLessThan(e);
        expect(lat).toBeGreaterThan(s);
        expect(lat).toBeLessThan(n);
      }
    }
    expect(dispatchBounds(null)).toBeUndefined();
  });

  it("draws the road where there is one and the straight line where there is not", () => {
    const legs = routeLegs(MAP);
    expect(legs).toHaveLength(12);
    expect(legs.every((l) => l.routed)).toBe(true);
    const straight = routeLegs({ ...MAP, legs: [{ ...MAP.legs[0], road: null }] });
    expect(straight[0].routed).toBe(false);
    expect(straight[0].path).toEqual([straight[0].depot, straight[0].target]);
    expect(unservedPlaces(MAP).length).toBeLessThanOrEqual(MAP.unassigned.length);
  });

  it("reads the IST clock from the cycle time", () => {
    expect(clockAt("2019-07-02T08:40:00+05:30", 0)).toBe("08:40");
    expect(clockAt("2019-07-02T08:40:00+05:30", 70)).toBe("09:50");
    expect(clockAt("2019-07-02T23:50:00+05:30", 20)).toBe("00:10");
    expect(clockAt(null, 10)).toBeNull();
  });

  it("calls the race against the first forecast minute above 45 cm", () => {
    const leg = MAP.legs.find((l) => l.pumpId === "P-08")!;
    expect(leg.windowBefore?.fromMin).toBe(70);
    expect(race(leg)).toEqual({ kind: "early", marginMin: 60 });
    expect(race({ ...leg, etaMin: 85 })).toEqual({ kind: "late", marginMin: 15 });
    expect(race({ ...leg, windowBefore: null }).kind).toBe("dry");
    expect(raceSentence(leg, MAP.cycleTs, 45)).toBe(
      "Arrives 08:50 (+10 min), 1 h before the water passes 45 cm at 09:50.",
    );
  });

  it("merges the timeline's steps above the line into runs of one depth band", () => {
    // 5-minute steps: 40 (below), 50, 55 (one 45-60 cm run), 62 (a >60 cm run), 44 (below), 58.
    // Step i is valid at (i + 1) * 5 min after the cycle, and its cell begins there.
    expect(cellRuns([40, 50, 55, 62, 44, 58], 45, 5)).toEqual([
      { fromMin: 10, toMin: 20, band: "4" },
      { fromMin: 20, toMin: 25, band: "5" },
      { fromMin: 30, toMin: 35, band: "4" },
    ]);
    expect(cellRuns([], 45, 5)).toEqual([]);
    // The pale cells begin at the minute the row's sentence names: the API's own window start.
    for (const leg of MAP.legs) {
      const first = cellRuns(leg.depthBeforeCm ?? [], 45, MAP.stepMin)[0];
      expect(first?.fromMin ?? null).toBe(leg.windowBefore?.fromMin ?? null);
    }
    // P-08 at 08:40: the water passes 45 cm at 09:50, and so does its first pale cell.
    const p08 = MAP.legs.find((l) => l.pumpId === "P-08")!;
    expect(
      clockAt(MAP.cycleTs, cellRuns(p08.depthBeforeCm ?? [], 45, MAP.stepMin)[0].fromMin),
    ).toBe("09:50");
    // Every leg of the demo cycle: the runs cover exactly the steps above the line.
    for (const leg of MAP.legs) {
      const series = leg.depthBeforeCm ?? [];
      const covered = cellRuns(series, 45, MAP.stepMin).reduce(
        (m, r) => m + r.toMin - r.fromMin,
        0,
      );
      expect(covered).toBe(series.filter((d) => d > 45).length * MAP.stepMin);
    }
  });

  it("captions a board column with its own peak, and the plan's only while it is the plan", () => {
    const column = {
      id: "street:Jijamata Road",
      title: "Jijamata Road",
      pumps: [],
      depthCm: { before: [10, 58.4, 40], after: [10, 46.2, 30] },
    };
    expect(depthCaption(column, false)).toBe("Peak 58 cm with no pump, next 3 h");
    expect(depthCaption(column, true)).toBe("Peak 46 cm with the plan, 58 cm without");
    expect(depthCaption({ ...column, depthCm: { before: [10, 58.4], after: null } }, true)).toBe(
      "Peak 58 cm with no pump, next 3 h",
    );
    expect(depthCaption({ ...column, depthCm: null }, true)).toBe(
      "No depth series for this place in this run",
    );
    // While the answer that carries the series is loading or failed, the screen's words, never
    // the settled "No depth series".
    expect(
      depthCaption({ ...column, depthCm: null }, true, "Loading this place's depth series"),
    ).toBe("Loading this place's depth series");
  });

  it("orders the rows by when the water arrives", () => {
    const starts = byFloodStart(MAP.legs).map((l) => l.windowBefore?.fromMin ?? Infinity);
    expect(starts).toEqual([...starts].sort((a, b) => a - b));
  });
});

describe("Jalayantra views", () => {
  it("prints the headline from the plan, and the benefit's caveat by its model", () => {
    render(<PumpHeadline plan={PLAN} />);
    const strip = screen.getByRole("region", { name: "What the plan buys" });
    expect(within(strip).getByText("985 min")).toBeInTheDocument();
    expect(within(strip).getByText("70 min")).toBeInTheDocument();
    expect(within(strip).getByText("Lokmanya Tilak Nagar")).toBeInTheDocument();
    // The phrase the demo script's 5:20 beat looks for.
    expect(strip).toHaveTextContent(/12 of 12 pumps assigned/);
    expect(benefitCaveat("emulator", MAP.emulator)).toMatch(/^Emulator estimate, lower bound/);
    expect(benefitCaveat("emulator", MAP.emulator)).toMatch(/RMSE 5.7 cm/);
    expect(plainMinutes(1234)).toBe("1,234 min");
    expect(benefitCaveat("reduced_model", null)).toMatch(/^Bathtub estimate/);
  });

  it("says nothing is sent before Optimise, then counts down as each pump arrives", () => {
    vi.useFakeTimers();
    const order = dispatchOrder(MAP);
    const { rerender } = render(
      <PumpHeadline plan={PLAN} order={order} clock={{ key: "idle", startMs: null, idle: true }} />,
    );
    const strip = screen.getByRole("region", { name: "What the plan buys" });
    expect(within(strip).getByText("Not sent")).toBeInTheDocument();
    expect(strip.querySelector("[data-number-flow]")).toBeNull();

    const start = performance.now();
    rerender(<PumpHeadline plan={PLAN} order={order} clock={{ key: "a", startMs: start }} />);
    // Sent, nobody there yet: the with-pumps figure starts at the no-pump total.
    expect(strip.querySelector("[data-number-flow]")).toHaveTextContent("985 min");
    // The first lorry in the plan's order arrives: its own saving comes off, and only its.
    const first = MAP.legs[0];
    act(() => {
      vi.advanceTimersByTime(arriveMs(0) + 1);
    });
    const total = PLAN.assignments.reduce((sum, a) => sum + a.minutesBefore, 0);
    expect(strip.querySelector("[data-number-flow]")).toHaveTextContent(
      `${Math.round(total - (first.minutesBefore - first.minutesAfter))} min`,
    );
    // Every lorry in: the plan's own total, and never a different one.
    act(() => {
      vi.advanceTimersByTime(arriveMs(MAP.legs.length));
    });
    expect(strip.querySelector("[data-number-flow]")).toHaveTextContent("70 min");
    vi.useRealTimers();
  });

  it("lists every place with a gauge, the biggest saving first", () => {
    render(<PlaceGauges legs={MAP.legs} thresholdCm={45} order={dispatchOrder(MAP)} />);
    const rows = within(screen.getByRole("list", { name: "Places the pumps go" })).getAllByRole(
      "button",
    );
    expect(rows).toHaveLength(12);
    expect(rows[0]).toHaveTextContent("Lokmanya Tilak Nagar");
    expect(screen.getAllByRole("img")[0]).toHaveAccessibleName(
      /Peak 51 cm with no pump, 46 cm with P-08/,
    );
    expect(gaugeScaleCm(MAP.legs)).toBe(70);
    expect(gaugePercent(35, 70)).toBe(50);
    expect(gaugePercent(90, 70)).toBe(100);
  });

  it("keeps a pressed row pressed: a hover lights the road, a press selects it", () => {
    function Harness() {
      const [selected, setSelected] = useState<string | null>(null);
      const [hovered, setHovered] = useState<string | null>(null);
      return (
        <>
          <p data-testid="lit">{hovered ?? selected ?? "none"}</p>
          <PlaceGauges
            legs={MAP.legs}
            thresholdCm={45}
            order={dispatchOrder(MAP)}
            selectedPumpId={selected}
            onSelect={setSelected}
            onHover={setHovered}
          />
          <ArrivalTimeline
            legs={MAP.legs}
            cycleTs={MAP.cycleTs}
            stepMin={MAP.stepMin}
            nSteps={MAP.nSteps}
            thresholdCm={45}
            selectedPumpId={selected}
            onSelect={setSelected}
            onHover={setHovered}
          />
        </>
      );
    }
    render(<Harness />);
    const gauges = within(screen.getByRole("list", { name: "Places the pumps go" }));
    const row = gauges.getAllByRole("button")[0];
    // A mouse click is always preceded by the pointer entering the row.
    fireEvent.mouseEnter(row);
    expect(row).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(row);
    expect(row).toHaveAttribute("aria-pressed", "true");
    // Leaving the row lets go of the hover, not of the press.
    fireEvent.mouseLeave(row);
    expect(row).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("lit")).toHaveTextContent("P-08");

    // The keyboard path: focus then Enter (a click) on a timeline row selects it too.
    const timeline = within(screen.getByRole("figure"));
    const tRow = timeline.getAllByRole("button").find((b) => !b.textContent?.includes("P-08"))!;
    fireEvent.focus(tRow);
    fireEvent.click(tRow);
    expect(tRow).toHaveAttribute("aria-pressed", "true");
    expect(row).toHaveAttribute("aria-pressed", "false");
    fireEvent.blur(tRow);
    expect(tRow).toHaveAttribute("aria-pressed", "true");
    // Pressing it again lets go.
    fireEvent.click(tRow);
    expect(tRow).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("lit")).toHaveTextContent("none");
  });

  it("draws one timeline row per pump with its verdict", () => {
    render(
      <ArrivalTimeline
        legs={MAP.legs}
        cycleTs={MAP.cycleTs}
        stepMin={MAP.stepMin}
        nSteps={MAP.nSteps}
        thresholdCm={45}
      />,
    );
    const figure = screen.getByRole("figure");
    expect(within(figure).getAllByRole("button")).toHaveLength(12);
    expect(within(figure).getAllByText(/before the water/).length).toBeGreaterThan(0);
    expect(
      within(figure).getByRole("img", { name: /Lokmanya Tilak Nagar: 1 h 55 min above 45 cm/ }),
    ).toBeInTheDocument();
  });
});
