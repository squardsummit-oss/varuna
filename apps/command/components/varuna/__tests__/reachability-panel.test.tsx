/**
 * SPEC.md 7.4 AC4: the reachability tab's 5/10/15-minute isochrones update when scrubbing, and
 * the collapse flag appears when the 15-minute catchment falls below 40 % of the dry baseline.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Place, ReachabilityResult } from "@/lib/api/route";
import { useRunStore } from "@/lib/stores/run";

const places: Place[] = [
  {
    id: "hospital-kem",
    name: "King Edward Memorial (KEM) Hospital, Parel",
    kind: "hospital",
    lon: 72.84218,
    lat: 19.001551,
  },
  {
    id: "fire_station-006",
    name: "Andheri Fire Station",
    kind: "fire_station",
    lon: 72.85,
    lat: 19.12,
  },
];

interface Asked {
  facility: string;
  at: string;
  profile: string;
  runId?: string;
}

const asked: Asked[] = [];
/** The 15-minute junction count the next answer carries, against 10,358 dry. */
let reached15 = 10_358;

const ring: [number, number][] = [
  [72.83, 18.99],
  [72.85, 18.99],
  [72.85, 19.01],
  [72.83, 18.99],
];

function answer(q: Asked): ReachabilityResult {
  const dry = 10_358;
  return {
    runId: q.runId ?? "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked",
    facility: { ...places[0], kind: "hospital" },
    validTs: q.at,
    profile: q.profile,
    collapsed: reached15 < 0.4 * dry,
    shareOfDry: reached15 / dry,
    bands: [5, 10, 15].map((minutes) => ({
      minutes,
      areaKm2: minutes,
      dryAreaKm2: minutes,
      nJunctions: minutes === 15 ? reached15 : dry,
      nJunctionsDry: dry,
      rings: [ring],
    })),
    ms: 900,
  };
}

vi.mock("@/lib/api/route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/route")>();
  return {
    ...actual,
    loadPlaces: vi.fn(async () => places),
    loadReachability: vi.fn(
      async (
        facility: string,
        at: string,
        profile: string,
        _signal?: AbortSignal,
        runId?: string,
      ) => {
        const q = { facility, at, profile, runId };
        asked.push(q);
        return answer(q);
      },
    ),
  };
});

const { ReachabilityPanel, SCRUB_SETTLE_MS, reachabilityStep } =
  await import("../reachability-panel");

const RUN_0840 = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";

beforeEach(() => {
  asked.length = 0;
  reached15 = 10_358;
  useRunStore.getState().clear();
});

afterEach(() => {
  useRunStore.getState().clear();
});

describe("reachabilityStep", () => {
  it("floors the scrub time to its five-minute forecast step, in IST", () => {
    expect(reachabilityStep("2019-07-02T08:43:20+05:30")).toBe("2019-07-02T08:40:00+05:30");
    expect(reachabilityStep("2019-07-02T08:45:00+05:30")).toBe("2019-07-02T08:45:00+05:30");
    expect(reachabilityStep("2019-07-02T03:14:00Z")).toBe("2019-07-02T08:40:00+05:30");
    expect(reachabilityStep("not a time")).toBe("not a time");
  });
});

describe("ReachabilityPanel", () => {
  it("asks again when the scrub crosses a forecast step, and not within one", async () => {
    const onIsochrones = vi.fn();
    const { rerender } = render(
      <ReachabilityPanel at="2019-07-02T08:40:00+05:30" onIsochrones={onIsochrones} />,
    );
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0]).toMatchObject({
      facility: "hospital-kem",
      at: "2019-07-02T08:40:00+05:30",
      profile: "ambulance",
    });
    await waitFor(() => expect(onIsochrones).toHaveBeenCalledTimes(1));
    expect(onIsochrones.mock.calls[0][0].map((b: { minutes: number }) => b.minutes)).toEqual([
      5, 10, 15,
    ]);

    rerender(<ReachabilityPanel at="2019-07-02T08:43:00+05:30" onIsochrones={onIsochrones} />);
    rerender(<ReachabilityPanel at="2019-07-02T08:44:59+05:30" onIsochrones={onIsochrones} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(asked).toHaveLength(1);

    rerender(<ReachabilityPanel at="2019-07-02T10:40:00+05:30" onIsochrones={onIsochrones} />);
    await waitFor(() => expect(asked).toHaveLength(2));
    expect(asked[1].at).toBe("2019-07-02T10:40:00+05:30");
    await waitFor(() => expect(onIsochrones).toHaveBeenCalledTimes(2));
  });

  it("a drag across several steps asks once, where it stops", async () => {
    const { rerender } = render(<ReachabilityPanel at="2019-07-02T10:15:00+05:30" />);
    rerender(<ReachabilityPanel at="2019-07-02T10:20:00+05:30" />);
    rerender(<ReachabilityPanel at="2019-07-02T10:25:00+05:30" />);
    rerender(<ReachabilityPanel at="2019-07-02T10:55:00+05:30" />);
    await waitFor(() => expect(asked).toHaveLength(1));
    await new Promise((r) => setTimeout(r, SCRUB_SETTLE_MS * 2));
    expect(asked).toHaveLength(1);
    expect(asked[0].at).toBe("2019-07-02T10:55:00+05:30");
  });

  it("measures on the run the console is drawing", async () => {
    useRunStore.getState().setRun({
      run_id: RUN_0840,
      city: "mumbai",
      cycle_ts: "2019-07-02T08:40:00+05:30",
      mode: "replay",
      replay_mode: "baked",
    });
    render(<ReachabilityPanel at="2019-07-02T11:30:00+05:30" />);
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0].runId).toBe(RUN_0840);
  });

  it("shows no collapse at 100 % of dry", async () => {
    render(<ReachabilityPanel at="2019-07-02T08:40:00+05:30" />);
    expect(await screen.findByText("15 minutes by ambulance")).toBeInTheDocument();
    expect(screen.getAllByText(/10,358 junctions of/)).toHaveLength(3);
    expect(screen.queryByText(/Catchment collapsed/)).not.toBeInTheDocument();
  });

  it("flags a collapse below 40 % of the dry 15-minute catchment", async () => {
    reached15 = 4_000; // 38.6 % of 10,358
    render(<ReachabilityPanel at="2019-07-02T11:30:00+05:30" />);
    expect(await screen.findByText(/Catchment collapsed/)).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /15 minute catchment, 39 per cent of dry/ }),
    ).toBeInTheDocument();
  });

  it("does not flag at 41 %", async () => {
    reached15 = 4_250; // 41.0 %
    render(<ReachabilityPanel at="2019-07-02T11:30:00+05:30" />);
    expect(await screen.findByText("15 minutes by ambulance")).toBeInTheDocument();
    expect(screen.queryByText(/Catchment collapsed/)).not.toBeInTheDocument();
  });
});
