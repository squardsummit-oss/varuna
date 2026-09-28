/**
 * The citizen route card: what it says, what it refuses to say, and how it is reached by keyboard
 * (UI_SPEC 4 and 10, TASKS D-12).
 *
 * The card is tested against two APIs at once: the one TECH_SPEC 3.2 specifies, with corridors
 * and reasons, and the one deployed today, which sends neither. Both must render an answer.
 */

import { createRequire } from "node:module";

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CorridorPicker } from "@/components/citizen/corridor-picker";
import {
  PumpsNearRoute,
  assignmentsNearRoute,
  metresBetween,
} from "@/components/citizen/pumps-near-route";
import { RouteAnswer } from "@/components/citizen/route-answer";
import type { PumpPlan } from "@/lib/api/pumps";
import type { RouteCorridor, RouteLeg, RoutePlan, RouteReason } from "@/lib/api/route";
import { renderWithProviders, stubFetch } from "@/lib/test-utils";

/** Two points on Dr Ambedkar Road, about 300 m apart, so "near the route" has a real geometry. */
const HINDMATA: [number, number] = [72.841, 19.012];
const UP_THE_ROAD: [number, number] = [72.8437, 19.0132];
const SION: [number, number] = [72.862, 19.039];
/** Andheri subway, 12 km north: far from every pump in the fixture plan. */
const ANDHERI: [number, number] = [72.844, 19.119];

function leg(overrides: Partial<RouteLeg> = {}): RouteLeg {
  return {
    minutes: 27,
    distanceM: 6100,
    maxDepthCm: 24,
    depart: "2019-07-02T08:40:00+05:30",
    arrive: "2019-07-02T09:07:00+05:30",
    safeUntil: "2019-07-02T08:05:00+05:30",
    path: [HINDMATA, UP_THE_ROAD],
    streets: ["Dr Ambedkar Road"],
    ...overrides,
  };
}

function plan(overrides: Partial<RoutePlan> = {}): RoutePlan {
  return {
    runId: "MUM-20190702T0840-sky1.0-twin1.0-flash0.3-baked",
    profile: "car",
    departAt: "2019-07-02T08:40:00+05:30",
    naive: leg({ minutes: 21, safeUntil: null }),
    varuna: leg(),
    alternates: [],
    avoided: [],
    corridors: [],
    reasons: [],
    tripId: "trip-1",
    notes: [],
    ms: 85,
    ...overrides,
  };
}

const AVOIDED: RouteReason = {
  kind: "avoided",
  segmentId: "seg-1",
  name: "Dr Ambedkar Road",
  depthCm: 47,
  thresholdCm: 30,
  at: "2019-07-02T08:20:00+05:30",
  probability: 0.82,
};

const DESIGN: RouteReason = {
  kind: "design",
  segmentId: "seg-1",
  name: "Dr Ambedkar Road",
  designIntensityMmH: 25,
  forecastPeakMmH: 61,
};

const TIMING: RouteReason = {
  kind: "timing",
  segmentId: "seg-2",
  name: "Lady Jamshedji Road",
  dryUntil: "2019-07-02T07:55:00+05:30",
  dryBelowCm: 5,
  depthCm: 47,
  thresholdCm: 30,
  at: "2019-07-02T08:20:00+05:30",
};

const CLOSURE: RouteReason = {
  kind: "closure",
  segmentId: "seg-3",
  name: "Sion Road",
  reason: "water main work",
  user: "the ward officer",
  at: "2019-07-02T08:12:00+05:30",
  until: null,
};

function corridor(id: string, label: string, share: number, assigned = false): RouteCorridor {
  return {
    id,
    label,
    route: leg({ minutes: share > 0.4 ? 27 : 31 }),
    share,
    assigned,
    capacityScore: share * 100,
    maxProbability: 0.18,
  };
}

const CORRIDORS = [
  corridor("c-a", "A", 0.6, true),
  corridor("c-b", "B", 0.3),
  corridor("c-c", "C", 0.1),
];

const EMULATOR_PLAN: PumpPlan = {
  runId: "MUM-20190702T0840-sky1.0-twin1.0-flash0.3-baked",
  thresholdCm: 45,
  benefitLabel: "Flash-lite emulator re-run with the pump's outflow",
  inventory: "synthetic",
  pumps: [],
  assignments: [
    {
      pumpId: "P-05",
      capacityM3PerHour: 250,
      depot: "Parel depot",
      targetId: "hindmata",
      targetName: "Hindmata junction",
      lon: HINDMATA[0],
      lat: HINDMATA[1],
      etaMin: 25,
      minutesBefore: 95,
      minutesAfter: 25,
      minutesSaved: 70,
    },
    {
      pumpId: "P-12",
      capacityM3PerHour: 250,
      depot: "Sion depot",
      targetId: "sion",
      targetName: "Sion Circle",
      lon: SION[0],
      lat: SION[1],
      etaMin: 18,
      minutesBefore: 60,
      minutesAfter: 20,
      minutesSaved: 40,
    },
  ],
  unassigned: [],
  totalMinutesSaved: 110,
};

describe("the two ETAs", () => {
  it("never shows one number without its comparison", () => {
    renderWithProviders(<RouteAnswer plan={plan()} profile="car" pumpPlan={null} />);
    expect(screen.getByText("Shortest way: 21 min · Safe way: 27 min (+6 min)")).toBeVisible();
  });

  it("says so when the two ways cost the same", () => {
    const same = plan({ naive: leg({ minutes: 27 }) });
    renderWithProviders(<RouteAnswer plan={same} profile="car" pumpPlan={null} />);
    expect(screen.getByText(/no extra time/)).toBeVisible();
  });

  it("refuses the trip in the reader's own words when no safe way exists", () => {
    renderWithProviders(
      <RouteAnswer plan={plan({ varuna: null })} profile="car" pumpPlan={null} />,
    );
    expect(screen.getByText(/no safe way through for a car/)).toBeVisible();
    expect(screen.queryByRole("radiogroup")).toBeNull();
  });
});

describe("why this way", () => {
  it("renders one sentence per reason kind, worst first", () => {
    renderWithProviders(
      <RouteAnswer
        plan={plan({ reasons: [DESIGN, TIMING, AVOIDED, CLOSURE] })}
        profile="car"
        pumpPlan={null}
      />,
    );
    const items = screen.getAllByRole("listitem").map((node) => node.textContent);
    expect(items[0]).toBe("Sion Road: closed by the ward officer at 08:12 — water main work.");
    expect(items[1]).toBe(
      "Avoids Dr Ambedkar Road — 47 cm at 08:20, deeper than a car can cross (30 cm).",
    );
    expect(items[2]).toBe(
      "Your road stays under 5 cm until 07:55, then rises to 47 cm by 08:20 — deeper than a car can cross (30 cm).",
    );
    expect(items[3]).toBe(
      "The drain under Dr Ambedkar Road was sized for 25 mm of rain an hour; this cycle peaks at 61 mm an hour.",
    );
  });

  it("drops a reason whose number is missing instead of softening it", () => {
    renderWithProviders(
      <RouteAnswer
        plan={plan({ reasons: [{ ...AVOIDED, depthCm: undefined }, DESIGN] })}
        profile="car"
        pumpPlan={null}
      />,
    );
    expect(screen.queryByText(/Avoids Dr Ambedkar Road/)).toBeNull();
    expect(screen.queryByText(/undefined/)).toBeNull();
    expect(screen.queryByText(/may flood|could flood|might/i)).toBeNull();
    expect(screen.getByText(/was sized for 25 mm of rain an hour/)).toBeVisible();
  });

  it("hides the whole block when no reason survives", () => {
    renderWithProviders(
      <RouteAnswer
        plan={plan({ reasons: [{ ...CLOSURE, reason: undefined }] })}
        profile="car"
        pumpPlan={null}
      />,
    );
    expect(screen.queryByText("Why this way")).toBeNull();
  });
});

describe("the corridor picker", () => {
  it("is a radio group whose chips carry their letter and share", () => {
    renderWithProviders(<CorridorPicker corridors={CORRIDORS} />);
    const group = screen.getByRole("radiogroup", { name: "Which safe road you are on" });
    expect(within(group).getByText("A · 6 in 10")).toBeVisible();
    expect(within(group).getByText("B · 3 in 10")).toBeVisible();
    expect(within(group).getByText("C · 1 in 10")).toBeVisible();
  });

  it("announces the assignment in words rather than only in colour", () => {
    renderWithProviders(<CorridorPicker corridors={CORRIDORS} />);
    expect(screen.getByRole("status")).toHaveTextContent("You are on corridor A of 3.");
    expect(screen.getByRole("radio", { name: /Corridor A/ })).toBeChecked();
  });

  it("says the split is a policy and not a traffic count", () => {
    renderWithProviders(<CorridorPicker corridors={CORRIDORS} />);
    expect(
      screen.getByText(
        "We spread drivers across three safe roads so the safe road does not become the next jam. The split is our policy, not a measured traffic count.",
      ),
    ).toBeVisible();
  });

  it("is reachable and changeable from the keyboard alone", async () => {
    const onPick = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<CorridorPicker corridors={CORRIDORS} onPick={onPick} />);

    await user.tab();
    expect(screen.getByRole("radio", { name: /Corridor A/ })).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    expect(onPick).toHaveBeenCalledWith("c-b");
    expect(screen.getByRole("radio", { name: /Corridor B/ })).toHaveFocus();
  });

  it("draws nothing when the run offered only one way", () => {
    const { container } = renderWithProviders(<CorridorPicker corridors={[CORRIDORS[0]!]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("changes the route the rest of the card explains", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <RouteAnswer plan={plan({ corridors: CORRIDORS })} profile="car" pumpPlan={null} />,
    );
    expect(screen.getByText(/Safe way: 27 min/)).toBeVisible();
    await user.click(screen.getByRole("radio", { name: /Corridor C/ }));
    expect(screen.getByText(/Safe way: 31 min/)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("You chose corridor C of 3.");
  });
});

describe("pumps near the route", () => {
  it("shows only the assignment whose junction is on the route, with the emulator chip", () => {
    renderWithProviders(<PumpsNearRoute route={leg()} plan={EMULATOR_PLAN} />);
    expect(
      screen.getByText("P-05 at Hindmata junction — expected to remove 1 h 10 min above 45 cm"),
    ).toBeVisible();
    expect(screen.getByText("Emulator estimate")).toBeVisible();
    expect(screen.queryByText(/P-12/)).toBeNull();
  });

  it("says bathtub when the plan says bathtub, rather than laundering it", () => {
    const bathtub: PumpPlan = {
      ...EMULATOR_PLAN,
      benefitLabel: "Bathtub estimate, not a physics run",
    };
    renderWithProviders(<PumpsNearRoute route={leg()} plan={bathtub} />);
    expect(screen.getByText("Bathtub estimate")).toBeVisible();
    expect(screen.queryByText("Emulator estimate")).toBeNull();
  });

  it("names no model when the plan names none", () => {
    renderWithProviders(
      <PumpsNearRoute route={leg()} plan={{ ...EMULATOR_PLAN, benefitLabel: "" }} />,
    );
    expect(
      screen.getByText("This plan does not say which model produced the number."),
    ).toBeVisible();
    expect(screen.queryByText("Emulator estimate")).toBeNull();
  });

  it("says in one line when no pump serves the route", () => {
    renderWithProviders(<PumpsNearRoute route={leg({ path: [ANDHERI] })} plan={EMULATOR_PLAN} />);
    expect(
      screen.getByText("No pump in this cycle’s plan is sent to a junction on your route."),
    ).toBeVisible();
  });

  it("does not claim a dispatched pump changes the map", () => {
    renderWithProviders(<PumpsNearRoute route={leg()} plan={EMULATOR_PLAN} />);
    expect(screen.getByText(/changes this estimate, not the depth drawn on the map/)).toBeVisible();
    expect(screen.getByText(/Synthetic pump inventory/)).toBeVisible();
  });

  it("says when the cycle has no plan at all", () => {
    renderWithProviders(<PumpsNearRoute route={leg()} plan={null} />);
    expect(screen.getByText("This cycle has no pump plan.")).toBeVisible();
  });

  it("measures distance rather than matching names", () => {
    expect(Math.round(metresBetween(HINDMATA, UP_THE_ROAD))).toBeGreaterThan(200);
    expect(Math.round(metresBetween(HINDMATA, UP_THE_ROAD))).toBeLessThan(400);
    expect(Math.round(metresBetween(HINDMATA, SION))).toBeGreaterThan(3000);
    expect(assignmentsNearRoute(EMULATOR_PLAN, leg()).map((a) => a.pumpId)).toEqual(["P-05"]);
    expect(assignmentsNearRoute(EMULATOR_PLAN, null)).toEqual([]);
    expect(assignmentsNearRoute(null, leg())).toEqual([]);
  });
});

describe("against the API deployed today, which sends no corridors and no reasons", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", stubFetch({ "/v1/pumps": { status: 404, body: {} } }));
  });

  it("still answers with the two ETAs and claims nothing else", async () => {
    renderWithProviders(<RouteAnswer plan={plan()} profile="car" />);
    expect(screen.getByText("Shortest way: 21 min · Safe way: 27 min (+6 min)")).toBeVisible();
    expect(screen.queryByText("Why this way")).toBeNull();
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(await screen.findByText("This cycle has no pump plan.")).toBeVisible();
  });

  it("still says when to leave, because safe_until is in every route", () => {
    renderWithProviders(<RouteAnswer plan={plan()} profile="car" pumpPlan={null} />);
    expect(
      screen.getByText(
        /Leave before 08:05 — after that this cycle no longer shows the whole route passable for a car\./,
      ),
    ).toBeVisible();
  });

  it("says nothing about leaving when the run gives no safe-until", () => {
    renderWithProviders(
      <RouteAnswer
        plan={plan({ varuna: leg({ safeUntil: null }) })}
        profile="car"
        pumpPlan={null}
      />,
    );
    expect(screen.queryByText(/Leave before/)).toBeNull();
  });
});

/**
 * axe over the rendered card.
 *
 * `axe-core` is not a dependency of `apps/command` - it arrives under `@axe-core/playwright`,
 * which the e2e suite uses - and adding one would touch `package.json` and the lockfile, which
 * this task does not own. It is resolved from that package's own folder instead.
 *
 * Two rule classes are turned off because jsdom cannot answer them, and a rule that cannot run is
 * worse than an absent one: `color-contrast` and `target-size` both need layout and real CSS,
 * and `vitest.config.ts` loads no CSS at all. Those two are the browser suite's job (P10.3 runs
 * axe over thirteen screens; `/dashboard` joins them with D-11). Everything axe can answer here -
 * accessible names, ARIA validity, duplicate ids, list and heading structure, nested controls -
 * is answered.
 *
 * The count of passing checks is asserted too, so a run that inspected nothing cannot read as a
 * clean one.
 */
describe("axe", () => {
  const RULES_JSDOM_CANNOT_ANSWER = { "color-contrast": { enabled: false } };

  /** Only the part of axe's surface used here; the package has no types on this resolution path. */
  interface AxeCore {
    run(
      context: Element,
      options: { rules: Record<string, { enabled: boolean }> },
    ): Promise<{ violations: { id: string; nodes: unknown[] }[]; passes: unknown[] }>;
  }

  async function violationsIn(container: HTMLElement) {
    const here = createRequire(import.meta.url);
    const axe = createRequire(here.resolve("@axe-core/playwright"))("axe-core") as AxeCore;
    const results = await axe.run(container, { rules: RULES_JSDOM_CANNOT_ANSWER });
    expect(results.passes.length).toBeGreaterThan(0);
    return results.violations.map((v) => `${v.id}: ${v.nodes.length} node(s)`);
  }

  it("finds nothing on the full card", async () => {
    const { container } = renderWithProviders(
      <RouteAnswer
        plan={plan({ corridors: CORRIDORS, reasons: [AVOIDED, DESIGN, TIMING, CLOSURE] })}
        profile="car"
        pumpPlan={EMULATOR_PLAN}
      />,
    );
    expect(await violationsIn(container)).toEqual([]);
  }, 30_000);

  it("finds nothing on the card an older API produces", async () => {
    const { container } = renderWithProviders(
      <RouteAnswer plan={plan()} profile="car" pumpPlan={null} />,
    );
    expect(await violationsIn(container)).toEqual([]);
  }, 30_000);
});
