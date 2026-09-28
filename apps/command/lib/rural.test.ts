import { describe, expect, it } from "vitest";

import type { RouteLeg, RoutePlan } from "@/lib/api/route";
import { shareInTen } from "@/lib/explain";
import {
  DEFAULT_VEHICLE,
  OPENING_SIM_TIME,
  RURAL_VEHICLES,
  buildAdvisory,
  detourStreet,
  etaComparison,
  forecastHorizon,
  insideBbox,
  parsePoint,
  parseVehicle,
  passableUntil,
  resolvePlace,
  sameRoad,
  shareLink,
  worstAvoided,
  type Bbox,
  type RuralPlaceRow,
} from "@/lib/rural";
import { DEFAULT_SIM_TIME } from "@/lib/stores/replay";

/** Mumbai's own bounding box, as `GET /v1/cities` reports it from `configs/mumbai.yaml`. */
const MUMBAI: Bbox = [72.815, 18.995, 72.905, 19.135];

const PLACES: RuralPlaceRow[] = [
  { id: "MUM-HS-05", name: "Sion Circle", lon: 72.863491, lat: 19.042733 },
  { id: "MUM-HS-08", name: "Kurla, LBS Marg", lon: 72.881638, lat: 19.081654 },
  { id: "MUM-HS-20", name: "Kamani junction, LBS Marg (Kurla)", lon: 72.887116, lat: 19.085164 },
  // Spelled exactly as `GET /v1/route/facilities` spells it. The fixture used to say "KEM
  // Hospital", which is what a person types and not what the register holds, and that gap is
  // what hid the defect this file now pins.
  {
    id: "hospital-001",
    name: "King Edward Memorial (KEM) Hospital, Parel",
    lon: 72.84218,
    lat: 19.001551,
  },
  { id: "hospital-002", name: "LTMG Hospital, Sion", lon: 72.862, lat: 19.0411 },
];

function leg(overrides: Partial<RouteLeg> = {}): RouteLeg {
  return {
    minutes: 21,
    distanceM: 6800,
    maxDepthCm: 4,
    depart: "2019-07-02T08:40:00+05:30",
    arrive: "2019-07-02T09:01:00+05:30",
    safeUntil: "2019-07-02T07:55:00+05:30",
    path: [],
    streets: ["Sion Circle", "Sion Panvel Highway"],
    ...overrides,
  };
}

function plan(overrides: Partial<RoutePlan> = {}): RoutePlan {
  return {
    runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    profile: "two_wheeler",
    departAt: "2019-07-02T08:40:00+05:30",
    naive: leg(),
    varuna: leg({ minutes: 27, streets: ["Sion Circle", "LBS Marg"] }),
    alternates: [],
    avoided: [],
    corridors: [],
    reasons: [],
    tripId: "t",
    notes: [],
    ms: 1,
    ...overrides,
  };
}

describe("the opening cycle", () => {
  it("is the same instant the console and the public map open on", () => {
    // `lib/stores/replay` is a client module, so the handler keeps its own copy of this string.
    // This is the assertion that keeps the copy honest; without it the two can drift silently.
    expect(OPENING_SIM_TIME).toBe(DEFAULT_SIM_TIME);
  });
});

describe("parseVehicle", () => {
  it("reads every word the picker offers", () => {
    for (const vehicle of RURAL_VEHICLES) {
      expect(parseVehicle(vehicle.word)).toEqual(vehicle);
    }
  });

  it("reads the spellings a forwarded link carries", () => {
    expect(parseVehicle("two_wheeler")?.profile).toBe("two_wheeler");
    expect(parseVehicle("Two Wheeler")?.profile).toBe("two_wheeler");
    expect(parseVehicle("pedestrian")?.word).toBe("on foot");
    expect(parseVehicle("on-foot")?.profile).toBe("pedestrian");
  });

  it("maps a truck to the bus, which is the threshold the router actually holds", () => {
    expect(parseVehicle("truck")?.profile).toBe("bus");
  });

  it("refuses a vehicle this build has no threshold for", () => {
    expect(parseVehicle("tractor")).toBeNull();
    expect(parseVehicle("")).toBeNull();
    expect(parseVehicle(null)).toBeNull();
  });

  it("defaults to a vehicle that is in the list", () => {
    expect(RURAL_VEHICLES).toContain(DEFAULT_VEHICLE);
  });
});

describe("parsePoint", () => {
  it("reads longitude then latitude", () => {
    expect(parsePoint("72.8635,19.0427")).toEqual([72.8635, 19.0427]);
    expect(parsePoint(" 72.8635 , 19.0427 ")).toEqual([72.8635, 19.0427]);
  });

  it("refuses anything that is not two numbers on the globe", () => {
    expect(parsePoint("Sion Circle")).toBeNull();
    expect(parsePoint("72.8635")).toBeNull();
    expect(parsePoint("72.8635,19.0427,3")).toBeNull();
    expect(parsePoint("200,19")).toBeNull();
  });
});

describe("resolvePlace", () => {
  it("takes a register name, however it is cased", () => {
    const resolved = resolvePlace("sion circle", PLACES, MUMBAI);
    expect(resolved).toMatchObject({ status: "ok" });
    if (resolved.status !== "ok") throw new Error("unreachable");
    expect(resolved.point.name).toBe("Sion Circle");
    expect(resolved.point.fromRegister).toBe(true);
  });

  it("takes a register id, which is what a machine-written link carries", () => {
    const resolved = resolvePlace("MUM-HS-08", PLACES, MUMBAI);
    expect(resolved.status).toBe("ok");
  });

  it("takes a coordinate inside the built area", () => {
    const resolved = resolvePlace("72.8635,19.0427", PLACES, MUMBAI);
    expect(resolved).toMatchObject({ status: "ok" });
    if (resolved.status !== "ok") throw new Error("unreachable");
    expect(resolved.point.fromRegister).toBe(false);
  });

  it("refuses a coordinate outside it rather than routing from the nearest node", () => {
    // Nagpur: inside India, nowhere near a drain graph VARUNA has built.
    expect(resolvePlace("79.088,21.146", PLACES, MUMBAI).status).toBe("outside");
  });

  it("finds a hospital by the words a person types, not the register's full spelling", () => {
    // The demo trip's own origin (SPEC.md 3.3). The register calls it "King Edward Memorial
    // (KEM) Hospital, Parel", so "KEM Hospital" is a substring of nothing and used to be refused.
    const resolved = resolvePlace("KEM Hospital", PLACES, MUMBAI);
    expect(resolved).toMatchObject({ status: "ok" });
    if (resolved.status !== "ok") throw new Error("unreachable");
    expect(resolved.point.name).toBe("King Edward Memorial (KEM) Hospital, Parel");
  });

  it("prefers a contiguous match over a scattered one", () => {
    // "Kurla, LBS Marg" contains this exactly; "Kamani junction, LBS Marg (Kurla)" holds the same
    // words scattered. Without the tiers, both would match and the trip would be refused.
    const resolved = resolvePlace("Kurla, LBS Marg", PLACES, MUMBAI);
    expect(resolved).toMatchObject({ status: "ok" });
    if (resolved.status !== "ok") throw new Error("unreachable");
    expect(resolved.point.name).toBe("Kurla, LBS Marg");
  });

  it("still refuses a word that matches several places, rather than picking one", () => {
    const resolved = resolvePlace("Hospital", PLACES, MUMBAI);
    expect(resolved.status).toBe("unknown");
  });

  it("refuses an ambiguous name and offers what it matched", () => {
    const resolved = resolvePlace("LBS Marg", PLACES, MUMBAI);
    expect(resolved).toMatchObject({ status: "unknown" });
    if (resolved.status !== "unknown") throw new Error("unreachable");
    expect(resolved.suggestions).toHaveLength(2);
  });

  it("refuses a name it does not hold, with no suggestions to invent", () => {
    const resolved = resolvePlace("Wardha", PLACES, MUMBAI);
    expect(resolved).toMatchObject({ status: "unknown", suggestions: [] });
  });

  it("says nothing was asked when nothing was typed", () => {
    expect(resolvePlace("", PLACES, MUMBAI).status).toBe("missing");
  });
});

describe("insideBbox", () => {
  it("counts the edges as inside", () => {
    expect(insideBbox([72.815, 18.995], MUMBAI)).toBe(true);
    expect(insideBbox([72.905, 19.135], MUMBAI)).toBe(true);
    expect(insideBbox([72.814, 19.0], MUMBAI)).toBe(false);
  });
});

describe("passableUntil", () => {
  const horizon = "2019-07-02T11:40:00+05:30";

  it("names the instant the road stops carrying the vehicle", () => {
    expect(passableUntil(leg(), horizon)).toEqual({ kind: "until", time: "07:55" });
  });

  it("calls the end of the forecast a horizon, not a promise", () => {
    expect(passableUntil(leg({ safeUntil: horizon }), horizon)).toEqual({
      kind: "horizon",
      time: "11:40",
    });
  });

  it("says nothing when the run carries no safe-until", () => {
    expect(passableUntil(leg({ safeUntil: null }), horizon).kind).toBe("unknown");
    expect(passableUntil(null, horizon).kind).toBe("unknown");
  });

  it("falls back to an instant when the horizon is unknown", () => {
    expect(passableUntil(leg(), null)).toEqual({ kind: "until", time: "07:55" });
  });
});

describe("forecastHorizon", () => {
  it("is the cycle plus its own steps", () => {
    expect(forecastHorizon("2019-07-02T08:40:00+05:30", 36, 5)).toBe("2019-07-02T06:10:00.000Z");
  });

  it("is nothing when the run meta is incomplete", () => {
    expect(forecastHorizon(null, 36, 5)).toBeNull();
    expect(forecastHorizon("2019-07-02T08:40:00+05:30", 0, 5)).toBeNull();
    expect(forecastHorizon("2019-07-02T08:40:00+05:30", 36, null)).toBeNull();
  });
});

describe("worstAvoided", () => {
  const avoided = (name: string, depthCm: number, at: string) => ({
    segmentId: name,
    name,
    depthCm,
    probability: 0.8,
    at,
    path: [] as [number, number][],
  });

  it("is the deepest water the router refused", () => {
    const result = worstAvoided(
      plan({
        avoided: [
          avoided("Dr Ambedkar Road", 24, "2019-07-02T08:20:00+05:30"),
          avoided("Kalachowki Road", 47, "2019-07-02T08:25:00+05:30"),
        ],
      }),
    );
    expect(result).toEqual({ depthCm: 47, street: "Kalachowki Road", at: "08:25" });
  });

  it("drops a record whose time the run did not give, rather than printing a hole", () => {
    expect(worstAvoided(plan({ avoided: [avoided("Somewhere", 47, "")] }))).toBeNull();
  });

  it("is nothing when the cycle put no water in the way", () => {
    expect(worstAvoided(plan())).toBeNull();
  });

  it("says a lane OSM does not name as where it is, never as 'an unnamed road'", () => {
    const lane = worstAvoided(
      plan({ avoided: [avoided("off Dr Ambedkar Road", 47, "2019-07-02T08:25:00+05:30")] }),
    );
    expect(lane?.street).toBe("a road off Dr Ambedkar Road");
    const older = worstAvoided(
      plan({ avoided: [avoided("Unnamed road", 47, "2019-07-02T08:25:00+05:30")] }),
    );
    expect(older?.street).toBe("a road");
  });
});

describe("the two roads", () => {
  it("names the first street the safe way uses and the shortest does not", () => {
    expect(detourStreet(leg(), leg({ streets: ["Sion Circle", "LBS Marg"] }))).toBe("LBS Marg");
  });

  it("names nothing when they share every street", () => {
    expect(detourStreet(leg(), leg())).toBeNull();
  });

  it("knows when the safe way is the shortest way", () => {
    expect(sameRoad(leg(), leg())).toBe(true);
    expect(sameRoad(leg(), leg({ minutes: 27 }))).toBe(false);
    expect(sameRoad(leg(), leg({ streets: ["Sion Circle"] }))).toBe(false);
  });
});

describe("etaComparison", () => {
  it("never gives one number without the other", () => {
    expect(etaComparison(leg(), leg({ minutes: 27 }))).toEqual({
      shortest: "21 min",
      safe: "27 min",
      difference: "+6 min",
    });
  });

  it("says so when the detour costs nothing", () => {
    expect(etaComparison(leg(), leg())?.difference).toBe("same time");
  });

  it("is nothing when only one route came back", () => {
    expect(etaComparison(leg(), null)).toBeNull();
    expect(etaComparison(null, leg())).toBeNull();
  });
});

describe("shareLink", () => {
  it("carries the whole query, so a forward reproduces the page", () => {
    const url = shareLink("https://varuna-dhrishta.vercel.app/", {
      from: "Sion Circle",
      to: "Kurla, LBS Marg",
      vehicle: "two-wheeler",
      run: "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
    });
    const parsed = new URL(url);
    expect(parsed.pathname).toBe("/rural");
    expect(parsed.searchParams.get("from")).toBe("Sion Circle");
    expect(parsed.searchParams.get("to")).toBe("Kurla, LBS Marg");
    expect(parsed.searchParams.get("v")).toBe("two-wheeler");
    expect(parsed.searchParams.get("run")).toBe("MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked");
  });

  it("leaves out a run it was not given", () => {
    const url = shareLink("http://localhost:3000", {
      from: "a",
      to: "b",
      vehicle: "car",
      run: null,
    });
    expect(new URL(url).searchParams.has("run")).toBe(false);
  });
});

describe("buildAdvisory", () => {
  const point = (name: string) => ({ name, lon: 72.86, lat: 19.04, fromRegister: true });
  const vehicle = RURAL_VEHICLES[0];

  it("reduces one route answer to the lines the page prints", () => {
    const advisory = buildAdvisory(
      plan({
        avoided: [
          {
            segmentId: "S1",
            name: "Dr Ambedkar Road",
            depthCm: 47,
            probability: 0.9,
            at: "2019-07-02T08:20:00+05:30",
            path: [],
          },
        ],
        reasons: [
          {
            kind: "avoided",
            segmentId: "S1",
            name: "Dr Ambedkar Road",
            depthCm: 47,
            thresholdCm: 15,
            at: "2019-07-02T08:20:00+05:30",
          },
        ],
        corridors: [
          {
            id: "a",
            label: "A",
            route: leg(),
            share: 0.6,
            assigned: true,
            capacityScore: 0.1,
            maxProbability: 0,
          },
          {
            id: "b",
            label: "B",
            route: leg({ minutes: 28 }),
            share: 0.4,
            assigned: false,
            capacityScore: 0.07,
            maxProbability: 0,
          },
        ],
      }),
      vehicle,
      point("Sion Circle"),
      point("Kurla, LBS Marg"),
      "2019-07-02T11:40:00+05:30",
    );

    expect(advisory.passable).toEqual({ kind: "until", time: "07:55" });
    expect(advisory.stopper).toEqual({ depthCm: 47, street: "Dr Ambedkar Road", at: "08:20" });
    expect(advisory.eta?.difference).toBe("+6 min");
    expect(advisory.detour).toBe("LBS Marg");
    expect(advisory.sameRoad).toBe(false);
    expect(advisory.yourRoad).toEqual(["Sion Circle", "Sion Panvel Highway"]);
    expect(advisory.reasons).toHaveLength(1);
    // The sentence is `lib/explain`'s, the same one `/dashboard` prints for this reason.
    expect(advisory.reasons[0].text).toContain("47 cm at 08:20");
    expect(advisory.reasons[0].text).toContain("a two-wheeler can cross (15 cm)");
    expect(advisory.corridors[0]).toEqual({
      label: "A",
      share: shareInTen(0.6),
      minutes: "21 min",
      assigned: true,
    });
  });

  it("drops a reason whose number the run did not supply", () => {
    const advisory = buildAdvisory(
      plan({
        reasons: [
          { kind: "avoided", segmentId: "S1", name: "Dr Ambedkar Road", depthCm: 47 },
          { kind: "design", segmentId: "S2", name: "LBS Marg", designIntensityMmH: 25 },
        ],
      }),
      vehicle,
      point("a"),
      point("b"),
      null,
    );
    expect(advisory.reasons).toHaveLength(0);
  });
});
