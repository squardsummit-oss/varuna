/**
 * The sentences under "Why this way", one test per kind, plus the rules that keep them honest
 * (UI_SPEC 4, TASKS D-12).
 *
 * Every expected string is written out in full rather than assembled from the module's own
 * helpers: a test that builds the sentence the same way the code does proves only that the code
 * is self-consistent. Snapshots here are literal.
 */

import { describe, expect, it } from "vitest";

import type { RouteReason } from "@/lib/api/route";
import {
  MAX_REASONS,
  UNNAMED_ROAD,
  explainReason,
  explainReasons,
  passableFor,
  shareInTen,
  spreadingDisclosure,
  streetName,
  vehicleNoun,
} from "@/lib/explain";

const AVOIDED: RouteReason = {
  kind: "avoided",
  segmentId: "seg-1",
  name: "Dr Ambedkar Road",
  depthCm: 47.3,
  thresholdCm: 30,
  at: "2019-07-02T08:20:00+05:30",
  probability: 0.82,
};

const DESIGN: RouteReason = {
  kind: "design",
  segmentId: "seg-1",
  name: "Dr Ambedkar Road",
  designIntensityMmH: 25,
  forecastPeakMmH: 61.4,
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

function textOf(reason: RouteReason, profile = "car"): string | null {
  return explainReason(reason, profile)?.text ?? null;
}

describe("one sentence per reason kind", () => {
  it("words an avoided street with its depth, its clock time and the vehicle it stops", () => {
    expect(textOf(AVOIDED)).toBe(
      "Avoids Dr Ambedkar Road — 47 cm at 08:20, deeper than a car can cross (30 cm).",
    );
  });

  it("words the design comparison from the drain's own sizing", () => {
    expect(textOf(DESIGN)).toBe(
      "The drain under Dr Ambedkar Road was sized for 25 mm of rain an hour; " +
        "this cycle peaks at 61 mm an hour.",
    );
  });

  it("words the timing of the road the route does take", () => {
    expect(textOf(TIMING)).toBe(
      "Your road stays under 5 cm until 07:55, then rises to 47 cm by 08:20 — " +
        "deeper than a car can cross (30 cm).",
    );
  });

  it("says water that rises without stopping this vehicle is still under its threshold", () => {
    expect(textOf({ ...TIMING, depthCm: 12 })).toBe(
      "Your road stays under 5 cm until 07:55, then rises to 12 cm by 08:20 — " +
        "still under the 30 cm that stops a car.",
    );
  });

  it("words a closure with the officer, the time and their own words", () => {
    expect(textOf(CLOSURE)).toBe(
      "Sion Road: closed by the ward officer at 08:12 — water main work.",
    );
  });

  it("carries a closure's reopening time when the officer gave one", () => {
    expect(textOf({ ...CLOSURE, until: "2019-07-02T10:00:00+05:30" })).toBe(
      "Sion Road: closed by the ward officer at 08:12 until 10:00 — water main work.",
    );
  });

  it("names no officer rather than inventing one", () => {
    expect(textOf({ ...CLOSURE, user: undefined })).toBe(
      "Sion Road: closed at 08:12 — water main work.",
    );
  });
});

describe("the vehicle the depth is measured against", () => {
  it("names a two-wheeler at its own threshold", () => {
    expect(textOf({ ...AVOIDED, thresholdCm: 15 }, "two-wheeler")).toBe(
      "Avoids Dr Ambedkar Road — 47 cm at 08:20, deeper than a two-wheeler can cross (15 cm).",
    );
  });

  it("says 'on foot' rather than 'a pedestrian'", () => {
    expect(vehicleNoun("pedestrian")).toBe("on foot");
    expect(textOf({ ...AVOIDED, thresholdCm: 30 }, "pedestrian")).toBe(
      "Avoids Dr Ambedkar Road — 47 cm at 08:20, deeper than is safe on foot (30 cm).",
    );
    expect(passableFor("pedestrian")).toBe("passable on foot");
    expect(passableFor("car")).toBe("passable for a car");
  });

  it("falls back to the profile the API sent when this build does not know it", () => {
    expect(vehicleNoun("tractor")).toBe("tractor");
  });
});

describe("a reason missing its number is dropped, never softened", () => {
  const cases: [string, RouteReason][] = [
    ["avoided with no depth", { ...AVOIDED, depthCm: undefined }],
    ["avoided with no threshold", { ...AVOIDED, thresholdCm: undefined }],
    ["avoided with no time", { ...AVOIDED, at: undefined }],
    ["avoided with an unparseable time", { ...AVOIDED, at: "soon" }],
    ["design with no design intensity", { ...DESIGN, designIntensityMmH: undefined }],
    ["design with no forecast peak", { ...DESIGN, forecastPeakMmH: undefined }],
    ["design with a peak of zero", { ...DESIGN, forecastPeakMmH: 0 }],
    ["timing with no dry-until", { ...TIMING, dryUntil: undefined }],
    ["timing with no depth", { ...TIMING, depthCm: undefined }],
    ["timing with no threshold", { ...TIMING, thresholdCm: undefined }],
    ["closure with no reason text", { ...CLOSURE, reason: undefined }],
    ["closure with blank reason text", { ...CLOSURE, reason: "   " }],
    ["closure with no time", { ...CLOSURE, at: undefined }],
  ];

  it.each(cases)("drops a %s", (_label, reason) => {
    expect(explainReason(reason, "car")).toBeNull();
  });

  it("drops it from the list too, leaving the reasons that are whole", () => {
    const list = explainReasons([{ ...AVOIDED, depthCm: undefined }, DESIGN], "car");
    expect(list).toHaveLength(1);
    expect(list[0]!.kind).toBe("design");
  });

  it("never emits a hedge in place of a number", () => {
    const list = explainReasons(
      cases.map(([, reason]) => reason),
      "car",
    );
    expect(list).toEqual([]);
  });
});

describe("order and cap", () => {
  it("shows at most four, worst first", () => {
    const deeper: RouteReason = { ...AVOIDED, segmentId: "seg-deep", depthCm: 70 };
    const shallower: RouteReason = { ...AVOIDED, segmentId: "seg-shallow", depthCm: 35 };
    const list = explainReasons([DESIGN, TIMING, shallower, deeper, CLOSURE], "car");
    expect(list.map((r) => r.kind)).toEqual(["closure", "avoided", "avoided", "timing"]);
    expect(list[1]!.segmentId).toBe("seg-deep");
    expect(list).toHaveLength(MAX_REASONS);
  });

  it("keeps the router's own order when two reasons are equally bad", () => {
    const first: RouteReason = { ...AVOIDED, segmentId: "first" };
    const second: RouteReason = { ...AVOIDED, segmentId: "second" };
    expect(explainReasons([first, second], "car").map((r) => r.segmentId)).toEqual([
      "first",
      "second",
    ]);
  });

  it("returns nothing for an API that sends no reasons", () => {
    expect(explainReasons(undefined, "car")).toEqual([]);
    expect(explainReasons(null, "car")).toEqual([]);
    expect(explainReasons([], "car")).toEqual([]);
  });
});

describe("street names", () => {
  it("reads a street with no name as 'a road', never as 'unnamed' or undefined", () => {
    expect(UNNAMED_ROAD).toBe("a road");
    expect(streetName(undefined)).toBe(UNNAMED_ROAD);
    expect(streetName("")).toBe(UNNAMED_ROAD);
    expect(streetName("Unnamed road")).toBe(UNNAMED_ROAD);
    expect(streetName("Unnamed way")).toBe(UNNAMED_ROAD);
    expect(streetName("None")).toBe(UNNAMED_ROAD);
    expect(textOf({ ...AVOIDED, name: "" })).toBe(
      "Avoids a road — 47 cm at 08:20, deeper than a car can cross (30 cm).",
    );
  });

  it("capitalises the no-name wording when it opens the sentence", () => {
    expect(textOf({ ...CLOSURE, name: "" })).toBe(
      "A road: closed by the ward officer at 08:12 — water main work.",
    );
  });

  it("says the API's proximity labels as proximity, never as the street's own name", () => {
    expect(streetName("off Dr Ambedkar Road")).toBe("a road off Dr Ambedkar Road");
    expect(streetName("Service road near Wadala Depot")).toBe("a service road near Wadala Depot");
    expect(streetName("Expressway ramp near Sion Circle")).toBe(
      "an expressway ramp near Sion Circle",
    );
    expect(textOf({ ...AVOIDED, name: "off Dr Ambedkar Road" })).toBe(
      "Avoids a road off Dr Ambedkar Road — 47 cm at 08:20, deeper than a car can cross (30 cm).",
    );
    expect(textOf({ ...CLOSURE, name: "Residential street in Mumbai" })).toBe(
      "A residential street in Mumbai: closed by the ward officer at 08:12 — water main work.",
    );
  });

  it("repairs a stringified Python list by taking the first name", () => {
    expect(streetName("['Dr Ambedkar Road', 'Kalachowki Road']")).toBe("Dr Ambedkar Road");
    expect(streetName('["Sion Road", "LBS Marg"]')).toBe("Sion Road");
    expect(streetName("[None, 'Milan Subway']")).toBe("Milan Subway");
    expect(streetName("[]")).toBe(UNNAMED_ROAD);
  });

  it("leaves a name that merely starts with a bracket alone", () => {
    expect(streetName("[Closed] Link Road")).toBe("[Closed] Link Road");
  });
});

describe("the corridor share, and what the screen says about it", () => {
  it("counts a share in tens", () => {
    expect(shareInTen(0.6)).toBe("6 in 10");
    expect(shareInTen(0.34)).toBe("3 in 10");
    expect(shareInTen(0.1)).toBe("1 in 10");
  });

  it("never says 'nobody' or 'everybody' for a share that is neither", () => {
    expect(shareInTen(0.04)).toBe("under 1 in 10");
    expect(shareInTen(0.97)).toBe("over 9 in 10");
    expect(shareInTen(1)).toBe("10 in 10");
  });

  it("prints no share at all when there is none to print", () => {
    expect(shareInTen(undefined)).toBeNull();
    expect(shareInTen(Number.NaN)).toBeNull();
    expect(shareInTen(0)).toBeNull();
    expect(shareInTen(-1)).toBeNull();
  });

  it("says the split is a policy, with the count that is on screen", () => {
    expect(spreadingDisclosure(3)).toBe(
      "We spread drivers across three safe roads so the safe road does not become the next jam. " +
        "The split is our policy, not a measured traffic count.",
    );
    expect(spreadingDisclosure(2)).toContain("two safe roads");
    expect(spreadingDisclosure(2)).toContain("not a measured traffic count");
  });
});
