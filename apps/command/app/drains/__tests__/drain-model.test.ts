/**
 * What `/drains` leads with, read out of a run as plain data (`drain-model.ts`).
 *
 * The figures are the 08:40 cycle of 2 July 2019 as Pulse re-analysed it on 2026-09-26: 201 pipes
 * moved (173 up, 28 down), 21 observations (10 traffic, 11 reports; 20 synthetic, 1 real),
 * capacity lost 28.408 % at the land-use prior and 28.496 % after learning, +55.6 m3/s. They are
 * fixtures here, not claims: each test pins how the screen reads a product, never a number.
 */

import { WebMercatorViewport } from "@deck.gl/core";
import { describe, expect, it } from "vitest";

import type { Bbox } from "@/components/map/basemap";

import type {
  AssimilatedObservation,
  DrainEdge,
  DrainHealth,
  DrainSummary,
  ObservationSet,
} from "@/lib/api/drains";

import {
  DRAIN_FIT_PADDING,
  DRAIN_MIN_ZOOM,
  deriveDrainStats,
  describeObservation,
  drainFrame,
  drainStory,
  learnedDrains,
  midpoint,
  nearbyStreetFinder,
  pipeTitle,
  rankPipeCards,
  stripLayout,
  STRIP_SPAN_MIN,
} from "../drain-model";

const CYCLE = "2019-07-02T08:40:00+05:30";

function edge(id: string, over: Partial<DrainEdge> = {}): DrainEdge {
  const prior = over.betaPrior ?? 0.2;
  const mean = over.betaMean ?? prior;
  return {
    id,
    street: null,
    displayName: null,
    locality: null,
    moved: false,
    path: [
      [72.84, 19.01],
      [72.842, 19.012],
    ],
    betaMean: mean,
    betaSd: 0.1,
    betaPrior: prior,
    betaDelta: mean - prior,
    capacityReductionPct: 40,
    diameterM: 0.6,
    observations: 0,
    explains: [],
    confidence: "inferred",
    lastUpdate: CYCLE,
    ...over,
  };
}

function obs(id: string, over: Partial<AssimilatedObservation> = {}): AssimilatedObservation {
  return {
    id,
    kind: "traffic",
    ts: CYCLE,
    place: "Dr Babasaheb Ambedkar Marg",
    locality: null,
    edgeId: null,
    depthCm: 20,
    depthSdCm: 8,
    speedKmh: 3,
    baselineKmh: 27.2,
    z: -6.3,
    chip: null,
    synthetic: true,
    betaBefore: 0.15,
    betaAfter: 0.22,
    innovationCm: 19.8,
    ...over,
  };
}

const SUMMARY: DrainSummary = {
  source: "product",
  nPipes: 49770,
  nMoved: 201,
  nMovedUp: 173,
  nMovedDown: 28,
  largestRise: {
    edge: "MUM-E037896",
    name: "off Eastern Freeway",
    locality: null,
    prior: 0.15,
    post: 0.4926,
  },
  largestFall: {
    edge: "MUM-E020001",
    name: "Sir Bhalchandra Road",
    locality: "near Dadar TT / Khodadad Circle",
    prior: 0.2,
    post: 0.0047,
  },
  capacityFullM3s: 63109,
  capacityLostPriorPct: 28.408,
  capacityLostPostPct: 28.496,
  capacityLearnedM3s: 55.6,
  nObs: 21,
  nObsByKind: { traffic: 10, report: 11 },
  nObsSynthetic: 20,
  nObsReal: 1,
  nMovedUnwritten: null,
  note: null,
};

function health(edges: DrainEdge[], summary: DrainSummary | null, nUpdated = 201): DrainHealth {
  return {
    runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    operator: "capacity_deficit",
    note: "",
    nEdges: 49770,
    nUpdated,
    edges,
    notes: [],
    summary,
  };
}

function observed(list: AssimilatedObservation[]): ObservationSet {
  return {
    runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    cycleTs: CYCLE,
    nTraffic: list.filter((o) => o.kind === "traffic").length,
    nReports: list.filter((o) => o.kind === "report").length,
    nAssimilated: list.length,
    nEdgesUpdated: 201,
    observations: list,
    disagreements: [],
    notes: [],
  };
}

describe("deriveDrainStats", () => {
  it("reads the product's summary, and names the larger of the rise and the fall", () => {
    const stats = deriveDrainStats(health([], SUMMARY), null);
    expect(stats.nMoved).toBe(201);
    expect(stats.nUp).toBe(173);
    expect(stats.nDown).toBe(28);
    expect(stats.partial).toBe(false);
    // With no observation list loaded, the summary's own counts stand.
    expect([stats.nObs, stats.nTraffic, stats.nReports]).toEqual([21, 10, 11]);
    expect([stats.nSynthetic, stats.nReal]).toEqual([20, 1]);
    expect(stats.capacity?.priorPct).toBeCloseTo(28.408);
    expect(stats.capacity?.learnedPoints).toBeCloseTo(0.088, 3);
    expect(stats.capacity?.learnedM3s).toBe(55.6);
    // |0.4926 - 0.15| = 0.3426 against |0.0047 - 0.20| = 0.1953: the rise.
    expect(stats.biggest?.edge).toBe("MUM-E037896");
    expect(stats.note).toBeNull();
  });

  it("takes the fall when it is the larger change", () => {
    const stats = deriveDrainStats(
      health([], { ...SUMMARY, largestRise: { ...SUMMARY.largestRise!, post: 0.2 } }),
      null,
    );
    expect(stats.biggest?.name).toBe("Sir Bhalchandra Road");
  });

  it("counts observations from the list the strip draws, so the tile and the strip agree", () => {
    const list = [obs("a"), obs("b", { kind: "report", synthetic: false, chip: "knee" })];
    const stats = deriveDrainStats(health([], SUMMARY), observed(list));
    expect([stats.nObs, stats.nTraffic, stats.nReports, stats.nReal]).toEqual([2, 1, 1, 1]);
  });

  it("says a rebuilt summary is rebuilt, and marks the counts partial", () => {
    const stats = deriveDrainStats(
      health([], {
        ...SUMMARY,
        source: "written_features",
        nMoved: 149,
        nMovedUp: 147,
        nMovedDown: 2,
        nMovedUnwritten: 52,
        note: "52 moved pipes were not written.",
      }),
      null,
    );
    // The tile counts every pipe Pulse moved; the up/down split is of the 149 written.
    expect(stats.nMoved).toBe(201);
    expect([stats.nUp, stats.nDown]).toEqual([147, 2]);
    expect(stats.partial).toBe(true);
    // The API's own sentence, once: it already says the figures are rebuilt.
    expect(stats.note).toBe("52 moved pipes were not written.");
    const bare = deriveDrainStats(
      health([], { ...SUMMARY, source: "written_features", note: null }),
      null,
    );
    expect(bare.note).toMatch(/Rebuilt from the pipes this run wrote/);
  });

  it("gives a rebuilt summary the network's size, not the count of pipes the run wrote", () => {
    // An API without the city build rebuilt its summary over the 6,000 written pipes only.
    const rebuilt = deriveDrainStats(
      health([], { ...SUMMARY, source: "written_features", nPipes: 6000 }),
      null,
    );
    expect(rebuilt.nPipes).toBe(49770);
    // The product's own summary is the bake's count over every pipe, and stands.
    expect(deriveDrainStats(health([], SUMMARY), null).nPipes).toBe(49770);
  });

  it("without a summary, counts only what the run wrote and leaves capacity unsplit", () => {
    const edges = [
      edge("up", { moved: true, betaPrior: 0.15, betaMean: 0.49 }),
      edge("down", { moved: true, betaPrior: 0.2, betaMean: 0.05 }),
      edge("market", { betaPrior: 0.35, betaMean: 0.35 }),
    ];
    const stats = deriveDrainStats(health(edges, null, 201), null);
    expect(stats.nMoved).toBe(201);
    expect([stats.nUp, stats.nDown]).toEqual([1, 1]);
    // Two written of 201 moved: the up/down split is of what was written, and says so.
    expect(stats.partial).toBe(true);
    expect(stats.capacity).toBeNull();
    expect(stats.biggest?.edge).toBe("up");
    expect(stats.note).toMatch(/no summary/);
  });
});

describe("rankPipeCards", () => {
  it("ranks by learned change, down moves included, and never names an unmoved prior", () => {
    const edges = [
      // The top of a blockage ranking is the 0.35 market prior, which nothing learned.
      edge("market", { betaPrior: 0.35, betaMean: 0.35, betaDelta: 0 }),
      edge("small", { moved: true, betaPrior: 0.2, betaMean: 0.25 }),
      edge("cleared", { moved: true, betaPrior: 0.2, betaMean: 0.0047 }),
      edge("trunk", { moved: true, betaPrior: 0.15, betaMean: 0.4926, diameterM: 3 }),
    ];
    const cards = rankPipeCards(health(edges, SUMMARY), null, 6);
    expect(cards.map((c) => c.id)).toEqual(["trunk", "cleared", "small"]);
    expect(cards[1].direction).toBe("down");
    expect(cards[0].lon).toBeCloseTo(72.841);
  });

  it("attaches the observation that moved the pipe most, where the bake recorded it", () => {
    const edges = [edge("E1", { moved: true, betaPrior: 0.15, betaMean: 0.4 })];
    const list = [
      obs("small", { edgeId: "E1", betaBefore: 0.15, betaAfter: 0.17 }),
      obs("large", { edgeId: "E1", betaBefore: 0.15, betaAfter: 0.4 }),
      obs("elsewhere", { edgeId: "E9", betaBefore: 0.1, betaAfter: 0.9 }),
    ];
    const [card] = rankPipeCards(health(edges, SUMMARY), observed(list));
    expect(card.movedBy?.id).toBe("large");
  });

  it("is empty with no run", () => {
    expect(rankPipeCards(null, null)).toEqual([]);
  });
});

describe("learnedDrains", () => {
  it("draws only the moved pipes, with their direction", () => {
    const edges = [
      edge("up", { moved: true, betaPrior: 0.15, betaMean: 0.3 }),
      edge("down", { moved: true, betaPrior: 0.2, betaMean: 0.1 }),
      edge("still"),
      edge("point", { moved: true, betaMean: 0.4, path: [[72.84, 19.01]] }),
    ];
    const drawn = learnedDrains(health(edges, null));
    expect(drawn.map((d) => [d.id, d.direction])).toEqual([
      ["up", "up"],
      ["down", "down"],
    ]);
    expect(drawn[0].beta).toBe(0.3);
    expect(learnedDrains(null)).toEqual([]);
  });
});

describe("midpoint", () => {
  it("is the middle vertex, or the middle of the middle segment", () => {
    expect(midpoint([])).toBeNull();
    expect(
      midpoint([
        [0, 0],
        [2, 2],
      ]),
    ).toEqual([1, 1]);
    expect(
      midpoint([
        [0, 0],
        [1, 5],
        [2, 0],
      ]),
    ).toEqual([1, 5]);
  });
});

describe("describeObservation", () => {
  it("says traffic in speeds and a report in its chip, never a zero it was not given", () => {
    expect(describeObservation(obs("t"))).toBe("Traffic at 3 km/h against 27 km/h usual");
    expect(describeObservation(obs("t", { speedKmh: null }))).toBe("Traffic anomaly");
    expect(describeObservation(obs("r", { kind: "report", chip: "knee", depthCm: 45 }))).toBe(
      "Citizen report, knee deep (45 cm)",
    );
  });
});

describe("stripLayout", () => {
  it("spans two hours to the cycle, and puts every mark at its members' own time", () => {
    const list = [
      obs("a"),
      obs("b"),
      obs("c"),
      obs("r", { kind: "report", ts: "2019-07-02T07:10:00+05:30" }),
    ];
    const { start, end, marks } = stripLayout(list, CYCLE);
    expect(end).toBe(Date.parse(CYCLE));
    expect(end - start).toBe(STRIP_SPAN_MIN * 60_000);
    // Three traffic anomalies stamped with the cycle's own minute are one mark with a count, at
    // the cycle, not three dots spread across a half hour none of them happened in.
    const traffic = marks.filter((m) => m.lane === "traffic");
    expect(traffic).toHaveLength(1);
    expect(traffic[0].x).toBe(1);
    expect(traffic[0].members.map((m) => m.id)).toEqual(["a", "b", "c"]);
    // 07:10 is 30 minutes into a strip that runs 06:40 to 08:40.
    const report = marks.find((m) => m.lane === "report");
    expect(report?.x).toBeCloseTo(0.25);
    expect(report?.members.map((m) => m.id)).toEqual(["r"]);
    // Oldest first.
    expect(marks.map((m) => m.lane)).toEqual(["report", "traffic"]);
  });

  it("keeps two kinds at one minute apart, and orders a group's members by id", () => {
    const { marks } = stripLayout([obs("z"), obs("m", { kind: "report" }), obs("a")], CYCLE);
    expect(marks.map((m) => [m.lane, m.members.map((o) => o.id)])).toEqual([
      ["report", ["m"]],
      ["traffic", ["a", "z"]],
    ]);
  });

  it("marks which way a lone observation moved its pipe, and counts a group's moves", () => {
    const single = stripLayout(
      [
        obs("up", { ts: "2019-07-02T08:00:00+05:30" }),
        obs("down", { ts: "2019-07-02T08:10:00+05:30", betaBefore: 0.2, betaAfter: 0.1 }),
        obs("unrecorded", { ts: "2019-07-02T08:20:00+05:30", betaBefore: null, betaAfter: null }),
      ],
      CYCLE,
    );
    expect(single.marks.map((m) => m.change)).toEqual(["up", "down", null]);

    const group = stripLayout(
      [
        obs("up"),
        obs("down", { betaBefore: 0.2, betaAfter: 0.1 }),
        obs("unrecorded", { betaBefore: null, betaAfter: null }),
      ],
      CYCLE,
    ).marks[0];
    expect(group.change).toBeNull();
    expect([group.up, group.down]).toEqual([1, 1]);
  });

  it("widens to an observation older than two hours rather than dropping it", () => {
    const { start, marks } = stripLayout(
      [obs("early", { ts: "2019-07-02T06:00:00+05:30" })],
      CYCLE,
    );
    expect(start).toBe(Date.parse("2019-07-02T06:00:00+05:30"));
    expect(marks[0].x).toBe(0);
  });

  it("ends at the latest observation when the run did not stamp its cycle", () => {
    const { end, marks } = stripLayout([obs("t", { ts: "2019-07-02T08:10:00+05:30" })], "");
    expect(end).toBe(Date.parse("2019-07-02T08:10:00+05:30"));
    expect(marks[0].x).toBe(1);
  });
});

describe("pipeTitle", () => {
  it("keeps a pipe's own name, from its street or the product's 'off <street>'", () => {
    expect(pipeTitle("MUM-E020001", "Sir Bhalchandra Road", "Service road near Wadala")).toBe(
      "Sir Bhalchandra Road",
    );
    expect(pipeTitle("MUM-E037896", "off Eastern Freeway")).toBe("off Eastern Freeway");
  });

  it("names a pipe no street names by its id and the nearest street, never 'Unnamed'", () => {
    expect(pipeTitle("MUM-E1", null, "off Dr Ambedkar Road")).toBe(
      "Pipe MUM-E1 off Dr Ambedkar Road",
    );
    expect(pipeTitle("MUM-E1", null, "Service road near Wadala Depot")).toBe(
      "Pipe MUM-E1 near Wadala Depot",
    );
    expect(pipeTitle("MUM-E1", null, "Dr Ambedkar Road")).toBe("Pipe MUM-E1 near Dr Ambedkar Road");
    // A city says nothing about where a pipe is; nor does nothing, or an older API's placeholder.
    expect(pipeTitle("MUM-E1", null, "Residential street in Mumbai")).toBe("Pipe MUM-E1");
    expect(pipeTitle("MUM-E1", null, null)).toBe("Pipe MUM-E1");
    expect(pipeTitle("MUM-E1", "Unnamed road", "Unnamed road")).toBe("Pipe MUM-E1");
    expect(pipeTitle("MUM-E1", "  ", "Street not recorded")).toBe("Pipe MUM-E1");
  });
});

describe("nearbyStreetFinder", () => {
  // Two streets 30 m and about 150 m north of the point, and one 2 km east.
  const at: [number, number] = [72.841, 19.011];
  const streets = [
    {
      path: [
        [72.8405, 19.01235],
        [72.8418, 19.01235],
      ] as [number, number][],
      displayName: "Service road near Wadala Depot",
    },
    {
      path: [
        [72.8405, 19.01127],
        [72.8418, 19.01127],
      ] as [number, number][],
      name: "Dr Ambedkar Road",
    },
    {
      path: [
        [72.86, 19.011],
        [72.861, 19.011],
      ] as [number, number][],
      displayName: "Main road near Sion Circle",
    },
  ];

  it("answers the nearest named street, measured to the line and not its vertices", () => {
    // Both of the near street's vertices are further than 30 m; the line passes 30 m north.
    expect(nearbyStreetFinder(streets)(at)).toBe("Dr Ambedkar Road");
  });

  it("answers nothing past the radius, and nothing with no streets", () => {
    expect(nearbyStreetFinder(streets)([72.841, 19.03])).toBeNull();
    expect(nearbyStreetFinder(streets, 20)(at)).toBeNull();
    expect(nearbyStreetFinder([])(at)).toBeNull();
  });

  it("skips a street with no usable label", () => {
    const unlabelled = [{ path: streets[1]!.path, displayName: "Unnamed road" }, streets[0]!];
    expect(nearbyStreetFinder(unlabelled)(at)).toBe("Service road near Wadala Depot");
  });

  it("is what rankPipeCards and deriveDrainStats place an unnamed pipe with", () => {
    const edges = [edge("MUM-E1", { moved: true, betaPrior: 0.15, betaMean: 0.4 })];
    const placeAt = nearbyStreetFinder(streets);
    const [card] = rankPipeCards(health(edges, null), null, 6, placeAt);
    // The fixture pipe's middle is 72.841, 19.011.
    expect(card.place).toBe("Dr Ambedkar Road");
    expect(deriveDrainStats(health(edges, null), null, placeAt).biggest?.place).toBe(
      "Dr Ambedkar Road",
    );
    // A pipe with a name of its own is not looked up.
    const named = [
      edge("MUM-E2", { moved: true, betaMean: 0.4, displayName: "off Eastern Freeway" }),
    ];
    expect(rankPipeCards(health(named, null), null, 6, placeAt)[0].place).toBeNull();
  });
});

describe("drainFrame: where /drains opens", () => {
  const AOI: Bbox = [
    [72.815, 18.995],
    [72.905, 19.135],
  ];
  const PANE = { width: 990, height: 520 };
  /** A 60 m pipe running east from a point, raised from 0.2 by `delta`. */
  const pipe = (lon: number, lat: number, delta = 0.2) => ({
    path: [
      [lon, lat],
      [lon + 0.0006, lat],
    ] as [number, number][],
    prior: 0.2,
    post: 0.2 + delta,
  });
  // Most of the learning in a Parel-to-Dadar band, a little of it 13 km north at Andheri.
  const parel = Array.from({ length: 40 }, (_, i) =>
    pipe(72.832 + (i % 8) * 0.004, 19.0 + Math.floor(i / 8) * 0.004),
  );
  const andheri = [pipe(72.845, 19.12, 0.05), pipe(72.85, 19.125, 0.05)];
  const fit = (box: Bbox, pane = PANE) =>
    new WebMercatorViewport(pane).fitBounds(box as [[number, number], [number, number]], {
      padding: DRAIN_FIT_PADDING,
    });

  it("is null with no story, so the map keeps everything it draws", () => {
    expect(drainFrame({ pipes: [], manholes: [] }, PANE, AOI)).toBeNull();
    expect(drainFrame(drainStory([], []), PANE, AOI)).toBeNull();
  });

  it("counts the learned pipes and the surcharging manholes equally, each set summing to one", () => {
    const story = drainStory(parel, [
      { lon: 72.84, lat: 19.01, q: 3 },
      { lon: 72.85, lat: 19.02, q: 1 },
    ]);
    expect(story.pipes.reduce((sum, p) => sum + p.weight, 0)).toBeCloseTo(1, 12);
    expect(story.manholes.map((p) => p.weight)).toEqual([0.75, 0.25]);
  });

  it("frames the dense band, not the far pipe, at a zoom where a pipe reads", () => {
    const box = drainFrame(drainStory([...parel, ...andheri]), PANE, AOI)!;
    const [[west, south], [east, north]] = box;
    // Holds every Parel pipe and none of Andheri's.
    for (const p of parel) {
      for (const [lon, lat] of p.path) {
        expect(lon).toBeGreaterThanOrEqual(west);
        expect(lon).toBeLessThanOrEqual(east);
        expect(lat).toBeGreaterThanOrEqual(south);
        expect(lat).toBeLessThanOrEqual(north);
      }
    }
    expect(north).toBeLessThan(19.12);
    expect(fit(box).zoom).toBeGreaterThanOrEqual(DRAIN_MIN_ZOOM - 0.05);
  });

  it("is shaped like the pane, centred on the learning, and mostly city", () => {
    const story = [...parel, ...andheri];
    const [[sw, ss], [se, sn]] = [
      [
        Math.min(...parel.flatMap((p) => p.path.map((q) => q[0]))),
        Math.min(...parel.flatMap((p) => p.path.map((q) => q[1]))),
      ],
      [
        Math.max(...parel.flatMap((p) => p.path.map((q) => q[0]))),
        Math.max(...parel.flatMap((p) => p.path.map((q) => q[1]))),
      ],
    ];
    for (const pane of [PANE, { width: 920, height: 400 }, { width: 1900, height: 1000 }]) {
      const box = drainFrame(drainStory(story), pane, AOI)!;
      const [[west, south], [east, north]] = box;
      // The pane's shape: the fit fills it both ways, so nothing the frame holds is cropped.
      const cos = Math.cos((((south + north) / 2) * Math.PI) / 180);
      const usable =
        (pane.width - DRAIN_FIT_PADDING.left - DRAIN_FIT_PADDING.right) /
        (pane.height - DRAIN_FIT_PADDING.top - DRAIN_FIT_PADDING.bottom);
      expect(((east - west) * cos) / (north - south)).toBeCloseTo(usable, 6);
      // Centred on the Parel band, give or take the trim.
      expect(Math.abs((west + east) / 2 - (sw + se) / 2)).toBeLessThan(0.004);
      expect(Math.abs((south + north) / 2 - (ss + sn) / 2)).toBeLessThan(0.004);
      // Never wider than the city, so what the view shows is mostly city.
      const view = fit(box, pane);
      const [viewWest] = view.unproject([0, pane.height / 2]);
      const [viewEast] = view.unproject([pane.width, pane.height / 2]);
      const inside = Math.min(viewEast, AOI[1][0]) - Math.max(viewWest, AOI[0][0]);
      expect(inside / (viewEast - viewWest)).toBeGreaterThan(0.75);
    }
  });

  it("lets the manholes choose where, and the drawn pipes what the box holds", () => {
    // Manholes spread right across the band, as the 500 hardest on 2 July are: the box stays on
    // the pipes, so the learning is centred rather than pushed to one edge.
    const spread = Array.from({ length: 30 }, (_, i) => ({
      lon: 72.816 + i * 0.003,
      lat: 19.004 + (i % 5) * 0.004,
      q: 1,
    }));
    const box = drainFrame(drainStory(parel, spread), PANE, AOI)!;
    const pipesOnly = drainFrame(drainStory(parel), PANE, AOI)!;
    expect(box[0][0]).toBeCloseTo(pipesOnly[0][0], 3);
    expect(box[1][0]).toBeCloseTo(pipesOnly[1][0], 3);
    // With nothing learned, the manholes alone are the story.
    const surchargeOnly = drainFrame(drainStory([], spread), PANE, AOI)!;
    expect(surchargeOnly[0][1]).toBeLessThanOrEqual(19.004);
    expect(surchargeOnly[1][1]).toBeGreaterThanOrEqual(19.02);
  });

  it("never zooms onto one pipe", () => {
    const box = drainFrame(drainStory([pipe(72.84, 19.01)]), PANE, AOI)!;
    const tallM = (box[1][1] - box[0][1]) * 110_540;
    expect(tallM).toBeGreaterThanOrEqual(1_199);
  });

  it("is deterministic", () => {
    const story = drainStory([...parel, ...andheri], [{ lon: 72.9, lat: 19.0, q: 1 }]);
    expect(drainFrame(story, PANE, AOI)).toEqual(
      drainFrame({ pipes: [...story.pipes], manholes: [...story.manholes] }, PANE, AOI),
    );
  });
});
