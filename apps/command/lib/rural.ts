/**
 * The low-bandwidth road advisory, in words (UI_SPEC 7, PRD 3.3, task D-16).
 *
 * `/rural` is one server-rendered page for a 2G phone and for a panchayat officer reading it
 * aloud: no map, no client JavaScript, under 30 KB. This module holds everything that can be
 * decided without a network or a DOM - resolving what the reader typed, and turning one route
 * answer into the lines the page prints - so all of it is unit-testable and none of it is
 * duplicated in the handler.
 *
 * **It borrows rather than re-words.** The sentences under "Why this way" come from
 * `lib/explain.ts`, the same module `/dashboard` uses, so the two screens cannot drift into two
 * vocabularies for one run. What is local here is only what the advisory says and the dashboard
 * does not: the "passable until" headline and the share link.
 *
 * Pure: no `fetch`, no `Date.now()`, no React. The handler supplies the run and the clock.
 */

import type { RouteLeg, RoutePlan } from "@/lib/api/route";
import {
  explainReasons,
  shareInTen,
  streetName,
  UNNAMED_ROAD,
  vehicleNoun,
  type ExplainedReason,
} from "@/lib/explain";
import { formatIst, formatMinutes, toDate } from "@/lib/format";
import type { PassabilityProfile } from "@/lib/ramps";

/**
 * The cycle `/rural` opens on when the query pins no run: the 06:40 cycle of the demo script.
 *
 * It mirrors `DEFAULT_SIM_TIME` in `lib/stores/replay.ts`, which `/console` and `/map` open on.
 * That module is a client store, so a server handler cannot import its constants; `rural.test.ts`
 * imports both and fails if they ever differ, which is the only way this copy stays a copy.
 */
export const OPENING_SIM_TIME = "2019-07-02T06:40:00+05:30";

/** A place a trip starts or ends at: a register entry, or a coordinate the reader typed. */
export interface RuralPoint {
  /** What the page prints for it. */
  name: string;
  lon: number;
  lat: number;
  /** True when it came from the city's own register rather than from two numbers in the URL. */
  fromRegister: boolean;
}

/** A place the register offers, in the shape `lib/api/route.ts` returns. */
export interface RuralPlaceRow {
  id: string;
  name: string;
  lon: number;
  lat: number;
}

/**
 * One vehicle, in the three vocabularies this screen has to speak at once.
 *
 * `word` is what a person says (UI_SPEC 9), `profile` is the key `POST /v1/route` takes, and
 * `uiProfile` is the key `lib/explain` and `lib/ramps` are written against. They differ, and a
 * screen that guessed between them would print "two_wheeler" at a reader.
 */
export interface RuralVehicle {
  word: string;
  profile: string;
  uiProfile: PassabilityProfile;
}

/** The five vehicles UI_SPEC 9 names, in the order a picker should offer them. */
export const RURAL_VEHICLES: readonly RuralVehicle[] = [
  { word: "two-wheeler", profile: "two_wheeler", uiProfile: "two-wheeler" },
  { word: "car", profile: "car", uiProfile: "car" },
  { word: "bus", profile: "bus", uiProfile: "bus" },
  { word: "ambulance", profile: "ambulance", uiProfile: "ambulance" },
  { word: "on foot", profile: "pedestrian", uiProfile: "pedestrian" },
];

/** The vehicle assumed when the query names none. The page always prints which one it used. */
export const DEFAULT_VEHICLE: RuralVehicle = RURAL_VEHICLES[1];

/** Spellings of a vehicle that are not its own word, so a forwarded link keeps working. */
const VEHICLE_ALIASES: Record<string, string> = {
  "two wheeler": "two-wheeler",
  two_wheeler: "two-wheeler",
  twowheeler: "two-wheeler",
  bike: "two-wheeler",
  scooter: "two-wheeler",
  motorcycle: "two-wheeler",
  truck: "bus",
  pedestrian: "on foot",
  walking: "on foot",
  walk: "on foot",
  foot: "on foot",
  "on-foot": "on foot",
  on_foot: "on foot",
};

/**
 * The vehicle a query names, or `null` when it names something this build has no threshold for.
 *
 * `truck` maps to the bus because the router gives both the same 45 cm and the same tolerance
 * (`services/route/varuna_route/profiles.py`); the page then says "bus", which is the word the
 * threshold belongs to, rather than inventing a truck profile the run never used.
 */
export function parseVehicle(raw: string | null | undefined): RuralVehicle | null {
  const key = (raw ?? "").trim().toLowerCase().replace(/\s+/g, " ");
  if (!key) return null;
  const word = VEHICLE_ALIASES[key] ?? key;
  return RURAL_VEHICLES.find((vehicle) => vehicle.word === word) ?? null;
}

/** `"72.8635,19.0427"` as a point, or `null` when it is not two numbers. */
export function parsePoint(raw: string | null | undefined): [number, number] | null {
  const parts = (raw ?? "").split(",");
  if (parts.length !== 2) return null;
  const lon = Number(parts[0].trim());
  const lat = Number(parts[1].trim());
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
  if (Math.abs(lon) > 180 || Math.abs(lat) > 90) return null;
  return [lon, lat];
}

/** `[minLon, minLat, maxLon, maxLat]`, as `GET /v1/cities` reports it. */
export type Bbox = [number, number, number, number];

export function insideBbox(point: readonly [number, number], bbox: Bbox): boolean {
  const [lon, lat] = point;
  return lon >= bbox[0] && lon <= bbox[2] && lat >= bbox[1] && lat <= bbox[3];
}

export type PlaceResolution =
  | { status: "missing" }
  | { status: "ok"; point: RuralPoint }
  /** A coordinate outside the built area: the advisory refuses rather than estimating. */
  | { status: "outside"; typed: string }
  | { status: "unknown"; typed: string; suggestions: string[] };

/** How many near-matches a refusal offers. More than this is a list, not a hint. */
export const MAX_SUGGESTIONS = 6;

/**
 * What the reader typed, resolved against the city's own registers.
 *
 * Two forms are accepted, and only two: a name from the register (hospitals, fire stations and
 * the chronic-spot register, all of which carry a `source_url` there) or `lon,lat`. A coordinate
 * outside the built bounding box is refused with UI_SPEC 9's sentence rather than routed from the
 * nearest node VARUNA happens to hold, which would answer a question about a place it has never
 * built. An ambiguous name is also refused, with the names it matched, because picking the first
 * of four would silently answer about a different street.
 *
 * **A name is matched word by word, not as one substring.** Matching the whole typed string as a
 * contiguous substring refused the demo trip's own origin: the register spells it "King Edward
 * Memorial (KEM) Hospital, Parel", so "KEM Hospital" - which is what SPEC.md 3.3 and every
 * other screen call it, and what a person types - contains no substring of that name and came
 * back "VARUNA holds no start called KEM Hospital". A contiguous match still wins where there is
 * one, so no name that resolved before resolves differently now.
 */
export function resolvePlace(
  raw: string | null | undefined,
  places: readonly RuralPlaceRow[],
  bbox: Bbox,
): PlaceResolution {
  const typed = (raw ?? "").trim();
  if (!typed) return { status: "missing" };

  const point = parsePoint(typed);
  if (point) {
    if (!insideBbox(point, bbox)) return { status: "outside", typed };
    return {
      status: "ok",
      point: {
        name: `${point[0]}, ${point[1]}`,
        lon: point[0],
        lat: point[1],
        fromRegister: false,
      },
    };
  }

  const key = typed.toLowerCase();
  const exact = places.find(
    (place) => place.id.toLowerCase() === key || place.name.toLowerCase() === key,
  );
  if (exact) return { status: "ok", point: asPoint(exact) };

  // Tried in order, and the first tier that matches anything is the answer. A contiguous match
  // beats a scattered one, so "Sion Circle" resolves to the junction of that name even though
  // its two words also appear apart in some longer entry.
  const contiguous = places.filter((place) => place.name.toLowerCase().includes(key));
  const words = key.split(/\s+/).filter(Boolean);
  const scattered = places.filter((place) => {
    const name = place.name.toLowerCase();
    return words.every((word) => name.includes(word));
  });
  const matched = contiguous.length ? contiguous : scattered;

  if (matched.length === 1) return { status: "ok", point: asPoint(matched[0]) };
  return {
    status: "unknown",
    typed,
    suggestions: matched.slice(0, MAX_SUGGESTIONS).map((place) => place.name),
  };
}

function asPoint(place: RuralPlaceRow): RuralPoint {
  return { name: place.name, lon: place.lon, lat: place.lat, fromRegister: true };
}

/**
 * "Shortest way: 21 min - Safe way: 27 min (+6 min)".
 *
 * Never one number without its comparison (UI_SPEC 4): a detour's cost is the whole question a
 * reader is weighing, and an ETA on its own hides it. `null` when the run gave only one of them.
 */
export function etaComparison(
  naive: RouteLeg | null,
  varuna: RouteLeg | null,
): { shortest: string; safe: string; difference: string } | null {
  if (!naive || !varuna) return null;
  const delta = Math.round(varuna.minutes) - Math.round(naive.minutes);
  const difference =
    delta === 0
      ? "same time"
      : delta > 0
        ? `+${formatMinutes(delta)}`
        : `-${formatMinutes(-delta)}`;
  return {
    shortest: formatMinutes(naive.minutes),
    safe: formatMinutes(varuna.minutes),
    difference,
  };
}

/**
 * How long the road carries this vehicle: an instant, the end of the forecast, or nothing known.
 *
 * `"horizon"` is the case that would otherwise be a lie. A run forecasts three hours, so a road
 * that never floods gets `safe_until` equal to the last step - and printing "passable until
 * 11:40" would promise a reader something about 11:41 that no artifact holds. The page says the
 * run stops there instead.
 */
export type PassableUntil =
  { kind: "until"; time: string } | { kind: "horizon"; time: string } | { kind: "unknown" };

export function passableUntil(leg: RouteLeg | null, horizonIso: string | null): PassableUntil {
  const until = toDate(leg?.safeUntil);
  if (!until) return { kind: "unknown" };
  const horizon = toDate(horizonIso);
  if (horizon && until.getTime() >= horizon.getTime()) {
    return { kind: "horizon", time: formatIst(horizonIso) };
  }
  return { kind: "until", time: formatIst(leg?.safeUntil) };
}

/** The last instant a run forecasts: its cycle time plus `n_steps` steps of `step_min`. */
export function forecastHorizon(
  cycleTs: string | null | undefined,
  nSteps: number | null | undefined,
  stepMin: number | null | undefined,
): string | null {
  const cycle = toDate(cycleTs);
  if (!cycle || !Number.isFinite(nSteps) || !Number.isFinite(stepMin)) return null;
  const steps = Number(nSteps);
  const minutes = Number(stepMin);
  if (steps <= 0 || minutes <= 0) return null;
  return new Date(cycle.getTime() + steps * minutes * 60_000).toISOString();
}

/** The water that stops this vehicle first, with the street and the time it arrives. */
export interface RuralStopper {
  depthCm: number;
  street: string;
  at: string;
}

/**
 * The deepest water the safe route refused, or `null` when this cycle put none in the way.
 *
 * `avoided` is the router's own record of what it would not cross and always carries all three
 * numbers. A record missing any of them is dropped, per the rule that a sentence with a hole in
 * it is not printed at all.
 */
export function worstAvoided(plan: Pick<RoutePlan, "avoided">): RuralStopper | null {
  let worst: RuralStopper | null = null;
  for (const avoided of plan.avoided) {
    if (!Number.isFinite(avoided.depthCm) || !toDate(avoided.at)) continue;
    const candidate: RuralStopper = {
      depthCm: Math.round(avoided.depthCm),
      street: streetName(avoided.name),
      at: formatIst(avoided.at),
    };
    if (!worst || candidate.depthCm > worst.depthCm) worst = candidate;
  }
  return worst;
}

/**
 * What stops this vehicle, and where: the water the route went around, or the water on it.
 *
 * The first version of this read `avoided[]` alone, and on the demo trip that was a real defect:
 * Sion Circle to Kurla has no detour on the 06:40 cycle, so nothing is avoided, and the page
 * printed "nothing rises above the depth that stops a two-wheeler" directly above a reason
 * saying the road reaches 21 cm against a 15 cm threshold. A road with no detour still floods;
 * the `timing` reason is where the router records that, and it is only a stopper when the depth
 * actually reaches the threshold the router costs on.
 */
export function stopper(plan: Pick<RoutePlan, "avoided" | "reasons">): RuralStopper | null {
  const avoided = worstAvoided(plan);
  if (avoided) return avoided;
  let worst: RuralStopper | null = null;
  for (const reason of plan.reasons) {
    if (reason.kind !== "timing" && reason.kind !== "avoided") continue;
    const depth = reason.depthCm;
    const threshold = reason.thresholdCm;
    if (!Number.isFinite(depth) || !Number.isFinite(threshold) || !toDate(reason.at)) continue;
    if ((depth as number) < (threshold as number)) continue;
    const candidate: RuralStopper = {
      depthCm: Math.round(depth as number),
      street: streetName(reason.name),
      at: formatIst(reason.at),
    };
    if (!worst || candidate.depthCm > worst.depthCm) worst = candidate;
  }
  return worst;
}

/**
 * The first street the safe way uses that the shortest way does not: the detour, in one name.
 *
 * `null` when the two routes share every street, which on a calm cycle is the honest answer and
 * is what makes the page say so rather than dress one road up as two.
 */
export function detourStreet(naive: RouteLeg | null, varuna: RouteLeg | null): string | null {
  if (!naive || !varuna) return null;
  const onNaive = new Set(naive.streets.map((street) => streetName(street)));
  for (const street of varuna.streets) {
    const name = streetName(street);
    if (!onNaive.has(name) && name !== UNNAMED_ROAD) return name;
  }
  return null;
}

/** True when the safe way is the shortest way: same distance, same minutes, same streets. */
export function sameRoad(naive: RouteLeg | null, varuna: RouteLeg | null): boolean {
  if (!naive || !varuna) return false;
  if (Math.round(naive.distanceM) !== Math.round(varuna.distanceM)) return false;
  if (Math.round(naive.minutes) !== Math.round(varuna.minutes)) return false;
  return naive.streets.join("|") === varuna.streets.join("|");
}

/** "a two-wheeler", "on foot" - the vehicle as `/dashboard` words it, from `lib/explain`. */
export function vehiclePhrase(vehicle: RuralVehicle): string {
  return vehicleNoun(vehicle.uiProfile);
}

/** "too deep for a two-wheeler", "too deep to cross on foot". */
export function tooDeepPhrase(vehicle: RuralVehicle): string {
  const noun = vehiclePhrase(vehicle);
  return noun === "on foot" ? "too deep to cross on foot" : `too deep for ${noun}`;
}

/**
 * The tail of "nothing on it rises above the depth that ...".
 *
 * Split from `vehiclePhrase` because "stops on foot" is not English; `lib/explain` makes the same
 * split for the same reason, and this keeps the two screens reading alike.
 */
export function stopsPhrase(vehicle: RuralVehicle): string {
  const noun = vehiclePhrase(vehicle);
  return noun === "on foot" ? "makes it unsafe on foot" : `stops ${noun}`;
}

/** The first few streets of a leg, named, for the "your road" line. */
export function roadNames(leg: RouteLeg | null, limit = 3): string[] {
  if (!leg) return [];
  const seen: string[] = [];
  for (const street of leg.streets) {
    const name = streetName(street);
    if (name === UNNAMED_ROAD || seen.includes(name)) continue;
    seen.push(name);
    if (seen.length >= limit) break;
  }
  return seen;
}

/** One corridor as the advisory lists it: a letter, a share in ten, and its own ETA. */
export interface RuralCorridor {
  label: string;
  share: string | null;
  minutes: string | null;
  assigned: boolean;
}

/** Everything the page prints about one trip, decided from one run. */
export interface RuralAdvisory {
  vehicle: RuralVehicle;
  from: RuralPoint;
  to: RuralPoint;
  runId: string;
  departAt: string;
  /** The shortest way: the road a reader takes unless told otherwise. */
  yourRoad: string[];
  passable: PassableUntil;
  stopper: RuralStopper | null;
  eta: { shortest: string; safe: string; difference: string } | null;
  detour: string | null;
  sameRoad: boolean;
  reasons: ExplainedReason[];
  corridors: RuralCorridor[];
  notes: string[];
}

/**
 * One route answer, reduced to the lines UI_SPEC 7 prints.
 *
 * `horizonIso` is the run's own forecast end, so "passable until" can tell an instant apart from
 * a horizon; pass `null` when the run meta could not be read and the page will say the shorter,
 * weaker thing rather than the stronger wrong one.
 */
export function buildAdvisory(
  plan: RoutePlan,
  vehicle: RuralVehicle,
  from: RuralPoint,
  to: RuralPoint,
  horizonIso: string | null,
): RuralAdvisory {
  return {
    vehicle,
    from,
    to,
    runId: plan.runId,
    departAt: plan.departAt,
    yourRoad: roadNames(plan.naive),
    passable: passableUntil(plan.naive, horizonIso),
    stopper: stopper(plan),
    eta: etaComparison(plan.naive, plan.varuna),
    detour: detourStreet(plan.naive, plan.varuna),
    sameRoad: sameRoad(plan.naive, plan.varuna),
    reasons: explainReasons(plan.reasons, vehicle.uiProfile),
    corridors: plan.corridors.map((corridor) => ({
      label: corridor.label,
      share: shareInTen(corridor.share),
      minutes: corridor.route ? formatMinutes(corridor.route.minutes) : null,
      assigned: corridor.assigned,
    })),
    notes: plan.notes,
  };
}

/**
 * The link at the foot of the page, carrying the whole query.
 *
 * A forwarded advisory has to reproduce the page it was forwarded from, so the run goes in the
 * link too: without it, a reader who opens the message an hour later gets a different cycle and
 * a different answer under the same words.
 */
export function shareLink(
  origin: string,
  query: { from: string; to: string; vehicle: string; run?: string | null; at?: string | null },
): string {
  const params = new URLSearchParams({ from: query.from, to: query.to, v: query.vehicle });
  if (query.run) params.set("run", query.run);
  if (query.at) params.set("at", query.at);
  return `${origin.replace(/\/+$/, "")}/rural?${params.toString()}`;
}
