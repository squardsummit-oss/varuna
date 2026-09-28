import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RouteCorridor, RouteLeg, RoutePlan } from "@/lib/api/route";

import { resetPhotorealVerdicts } from "@/lib/maps/photoreal";

import { CitizenMap, routeLines, STOPS_AT_CM } from "./citizen-map";

// The fallback path renders VARUNA's own deck.gl map, which would ask jsdom for a WebGL context.
// What this file tests is which path runs and what it says, not what deck draws.
vi.mock("@/components/map/city-map", () => ({
  CityMap: () => <div data-testid="varuna-map" />,
}));

const KEY = "NEXT_PUBLIC_GOOGLE_MAPS_API_KEY";

let errors: unknown[][];
let warnings: unknown[][];

beforeEach(() => {
  errors = [];
  warnings = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    errors.push(args);
  });
  vi.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
    warnings.push(args);
  });
  // No API in a unit test; the run load refuses and the map draws no water, which is a state the
  // screen already words.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 503 })),
  );
});

afterEach(() => {
  // A `ready` verdict is cached for the life of the module, so one test's Google must not decide
  // the next one's.
  resetPhotorealVerdicts();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CitizenMap without a Google key", () => {
  it("renders VARUNA's own map and says which map it is", async () => {
    vi.stubEnv(KEY, "");
    render(<CitizenMap profile="car" />);

    expect(await screen.findByTestId("varuna-map")).toBeInTheDocument();
    expect(screen.getByText(/showing VARUNA's own map/)).toBeInTheDocument();
  });

  it("logs nothing - an absent key is a configuration, not an error", async () => {
    vi.stubEnv(KEY, "undefined");
    render(<CitizenMap profile="two-wheeler" />);
    await screen.findByTestId("varuna-map");

    expect(errors).toEqual([]);
    expect(warnings).toEqual([]);
  });
});

describe("CitizenMap's photorealistic city", () => {
  it("offers the switch even without a Google key, and leaves the map alone until it is pressed", async () => {
    // SPEC.md 17 is satisfied by a switch that answers, not by one that is hidden: finding out
    // whether Google will serve tiles costs a request, so the control is offered and the answer
    // is printed when it is asked for.
    vi.stubEnv(KEY, "");
    render(<CitizenMap profile="car" />);

    const toggle = await screen.findByRole("switch", { name: /Photorealistic city/ });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByText(/photorealistic basemap is off/)).not.toBeInTheDocument();
  });

  it("names the missing key rather than emptying the map when it is switched on", async () => {
    vi.stubEnv(KEY, "");
    render(<CitizenMap profile="car" />);

    await userEvent.click(await screen.findByRole("switch", { name: /Photorealistic city/ }));

    // The fallback chain is untouched: VARUNA's own map is still drawn, and now says why the
    // photographed one is not.
    expect(screen.getByTestId("varuna-map")).toBeInTheDocument();
    expect(screen.getByText(/showing VARUNA's own map/)).toBeInTheDocument();
    expect(screen.getByText(/NEXT_PUBLIC_GOOGLE_MAPS_API_KEY/)).toBeInTheDocument();
  });

  it("names the disabled Map Tiles API when that is what Google says", async () => {
    // A 403 body of the shape Google sends when the Map Tiles API is switched off for a project.
    // It is not what this key gets today - 2026-09-23 it gets a 404, covered in the console's own
    // test - but it is the case the reader can actually fix, so it is worth pinning.
    vi.stubEnv(KEY, "test-key");
    resetPhotorealVerdicts();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("tile.googleapis.com")
          ? new Response(
              JSON.stringify({
                error: {
                  code: 403,
                  message: "Map Tiles API has not been used in project 1 before or it is disabled.",
                  status: "PERMISSION_DENIED",
                  details: [{ reason: "SERVICE_DISABLED" }],
                },
              }),
              { status: 403 },
            )
          : new Response("{}", { status: 503 }),
      ),
    );
    render(<CitizenMap profile="car" />);

    await userEvent.click(await screen.findByRole("switch", { name: /Photorealistic city/ }));

    expect(await screen.findByText(/Map Tiles API is not enabled/)).toBeInTheDocument();
    expect(errors).toEqual([]);
    expect(warnings).toEqual([]);
  });
});

describe("STOPS_AT_CM", () => {
  it("uses the same stopping depths the router and the public map use", () => {
    expect(STOPS_AT_CM).toEqual({
      "two-wheeler": 15,
      car: 30,
      bus: 45,
      pedestrian: 30,
    });
  });
});

function leg(path: [number, number][]): RouteLeg {
  return {
    minutes: 20,
    distanceM: 4000,
    maxDepthCm: 10,
    depart: "2019-07-02T08:40:00+05:30",
    arrive: "2019-07-02T09:00:00+05:30",
    safeUntil: null,
    path,
    streets: ["Dr Ambedkar Road"],
  };
}

function corridor(id: string, assigned: boolean): RouteCorridor {
  return {
    id,
    label: id.toUpperCase(),
    route: leg([
      [72.84, 19.01],
      [72.85, 19.02],
    ]),
    share: 0.5,
    assigned,
    capacityScore: 1,
    maxProbability: 0.1,
  };
}

const plan: RoutePlan = {
  runId: "MUM-x",
  profile: "car",
  departAt: "2019-07-02T08:40:00+05:30",
  naive: leg([
    [72.83, 19.0],
    [72.84, 19.01],
  ]),
  varuna: leg([
    [72.83, 19.0],
    [72.86, 19.03],
  ]),
  alternates: [],
  avoided: [
    {
      segmentId: "S-1",
      name: "Dr Ambedkar Road",
      depthCm: 47,
      probability: 0.8,
      at: "2019-07-02T08:20:00+05:30",
      path: [
        [72.841, 19.012],
        [72.842, 19.013],
      ],
    },
  ],
  corridors: [],
  reasons: [],
  tripId: null,
  notes: [],
  ms: 85,
};

describe("routeLines", () => {
  it("draws nothing before a trip is planned", () => {
    expect(routeLines(null, null, null)).toEqual([]);
  });

  it("draws the shortest way, what it avoided and the safe way", () => {
    const kinds = routeLines(plan, null, null).map((l) => l.kind);
    expect(kinds).toEqual(["naive", "avoided", "varuna"]);
  });

  it("puts the chosen corridor in the VARUNA line and dims the others", () => {
    const lines = routeLines(plan, [corridor("a", true), corridor("b", false)], null);
    expect(lines.filter((l) => l.kind === "varuna")).toHaveLength(1);
    expect(lines.filter((l) => l.kind === "alternate").map((l) => l.id)).toEqual(["corridor-b"]);
  });

  it("follows the reader's pick over the assigned corridor", () => {
    const lines = routeLines(plan, [corridor("a", true), corridor("b", false)], "b");
    expect(lines.filter((l) => l.kind === "alternate").map((l) => l.id)).toEqual(["corridor-a"]);
  });

  it("drops a leg the API sent with no geometry rather than drawing an empty path", () => {
    const empty: RoutePlan = { ...plan, naive: leg([]), avoided: [] };
    expect(routeLines(empty, null, null).map((l) => l.kind)).toEqual(["varuna"]);
  });
});
