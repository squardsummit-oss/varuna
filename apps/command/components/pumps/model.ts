/**
 * Pure helpers behind Jalayantra's dispatch view: nothing here renders, so everything here is
 * tested without a map or a DOM.
 */

import type { Bbox } from "@/components/map/basemap";
import type { PumpRouteLeg, UnservedPlace } from "@/components/map/layers/pump-routes";
import type { PumpLeg, PumpMap } from "@/lib/api/pumps";

/** Degrees added around the frame so a ring on its edge is whole: about 250 m. */
const FRAME_PAD_DEG = 0.0025;
/** The smallest side a frame has, about 1.8 km, so one short trip opens at street scale. */
const MIN_SPAN_DEG = 0.016;
/** Kilometres in a degree of latitude. */
const KM_PER_DEG = 111.32;
/**
 * A depot is part of the plan's picture when it lies within this of the box around the places:
 * 1.5 km, or 15 % of that box's diagonal on a plan that spreads across the city.
 */
const NEAR_KM_MIN = 1.5;
const NEAR_SHARE = 0.15;
/** With fewer places than this the whole trip is the picture, depot included. */
const CLUSTER_MIN_PLACES = 3;

type LonLat = [number, number];

function box(points: readonly LonLat[]): Bbox {
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  return [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
}

/** Kilometres from a point to a box; 0 inside it. */
function kmToBox([lon, lat]: LonLat, [[west, south], [east, north]]: Bbox): number {
  const kx = KM_PER_DEG * Math.cos((lat * Math.PI) / 180);
  const dx = Math.max(west - lon, 0, lon - east) * kx;
  const dy = Math.max(south - lat, 0, lat - north) * KM_PER_DEG;
  return Math.hypot(dx, dy);
}

function diagonalKm([[west, south], [east, north]]: Bbox): number {
  const kx = KM_PER_DEG * Math.cos((((south + north) / 2) * Math.PI) / 180);
  return Math.hypot((east - west) * kx, (north - south) * KM_PER_DEG);
}

/**
 * The frame the dispatch map opens on: the places the plan sends pumps to, and the depots near
 * them, tightly, so the lorries and their roads read at the opening zoom.
 *
 * A depot far from the rest is left out of the frame rather than allowed to pull it wide: on the
 * 08:40 cycle the Worli garage sits 4 km south of the nearest place, and framing it cost the
 * other eleven lorries a third of the map. Its lorry drives in from the frame's edge. A plan
 * with one or two places frames the whole trip, depot included, because there the trip is the
 * picture. Undefined when the plan has no coordinate at all.
 */
export function dispatchBounds(map: PumpMap | null): Bbox | undefined {
  if (!map) return undefined;
  const places: LonLat[] = [];
  const depots: LonLat[] = [];
  for (const leg of map.legs) {
    if (leg.target.lon != null && leg.target.lat != null)
      places.push([leg.target.lon, leg.target.lat]);
    if (leg.depot.lon != null && leg.depot.lat != null) depots.push([leg.depot.lon, leg.depot.lat]);
  }
  if (places.length === 0 && depots.length === 0) return undefined;

  let points: LonLat[];
  if (places.length < CLUSTER_MIN_PLACES) {
    points = [...places, ...depots];
  } else {
    const core = box(places);
    const reach = Math.max(NEAR_KM_MIN, NEAR_SHARE * diagonalKm(core));
    points = [...places, ...depots.filter((d) => kmToBox(d, core) <= reach)];
  }

  const [[west, south], [east, north]] = box(points);
  const cx = (west + east) / 2;
  const cy = (south + north) / 2;
  const halfW = Math.max(east - west, MIN_SPAN_DEG) / 2 + FRAME_PAD_DEG;
  const halfH = Math.max(north - south, MIN_SPAN_DEG) / 2 + FRAME_PAD_DEG;
  return [
    [cx - halfW, cy - halfH],
    [cx + halfW, cy + halfH],
  ];
}

/**
 * The legs the map can draw: both ends known. The road when there is one, else the line. `order`
 * is the leg's place in the plan, which is the order the lorries leave in (M33) and the order the
 * gauges drain in (M34), so the map and the gauges time from the same index.
 */
export function routeLegs(map: PumpMap | null): PumpRouteLeg[] {
  if (!map) return [];
  // A place whose series the API could not recompute is drawn at the least it is known to be:
  // above the line while the plan counts minutes above it, as the unserved places are. Its disc
  // then stays that colour when the pump arrives - the plan gives minutes, not a depth.
  const aboveLine = map.thresholdCm + 0.5;
  const out: PumpRouteLeg[] = [];
  for (const [order, leg] of map.legs.entries()) {
    const { depot, target } = leg;
    if (depot.lon == null || depot.lat == null || target.lon == null || target.lat == null) {
      continue;
    }
    const from: [number, number] = [depot.lon, depot.lat];
    const to: [number, number] = [target.lon, target.lat];
    const road = leg.road && leg.road.path.length > 1 ? leg.road.path : null;
    const knownBefore = leg.minutesBefore > 0 ? aboveLine : 0;
    out.push({
      pumpId: leg.pumpId,
      depot: from,
      target: to,
      targetName: leg.target.name,
      depotName: depot.name,
      path: road ?? [from, to],
      routed: road !== null,
      peakBeforeCm: leg.peakBefore?.depthCm ?? knownBefore,
      peakAfterCm: leg.peakAfter?.depthCm ?? (leg.peakBefore ? 0 : knownBefore),
      order,
    });
  }
  return out;
}

export function depotPoints(map: PumpMap | null): { name: string; position: [number, number] }[] {
  return (map?.depots ?? []).flatMap((d) =>
    d.lon != null && d.lat != null
      ? [{ name: d.name, position: [d.lon, d.lat] as [number, number] }]
      : [],
  );
}

export function unservedPlaces(map: PumpMap | null): UnservedPlace[] {
  return (map?.unassigned ?? []).flatMap((u) =>
    u.lon != null && u.lat != null
      ? [{ id: u.id, position: [u.lon, u.lat] as [number, number], minutesAbove: u.minutesAbove }]
      : [],
  );
}

/** Whether the pump is there before the water crosses 45 cm, and by how much. */
export interface Race {
  /** "unknown": the place has no depth series, so there is no crossing time to race. */
  kind: "early" | "late" | "dry" | "unknown";
  /** Minutes between arrival and the water crossing 45 cm; always positive. */
  marginMin: number;
}

/**
 * The race the timeline draws: the plan's ETA against the first forecast minute above 45 cm
 * without a pump. "dry" when the place never crosses (the plan would not have sent a pump).
 */
export function race(leg: PumpLeg): Race {
  // No series is not a dry place: the plan may count minutes above the line that the API could
  // not recompute, and "never above the line" beside "70 min above 45 cm" is the screen lying.
  if (leg.depthBeforeCm === null) return { kind: "unknown", marginMin: 0 };
  // The first forecast time above 45 cm, as the forecast states it; no interpolation into the
  // step before, which would claim a crossing time the forecast never gave.
  const crosses = leg.windowBefore?.fromMin;
  if (crosses == null) return { kind: "dry", marginMin: 0 };
  return leg.etaMin <= crosses
    ? { kind: "early", marginMin: crosses - leg.etaMin }
    : { kind: "late", marginMin: leg.etaMin - crosses };
}

/** "08:40" plus `minutes`, as the IST clock reads it; the cycle time is ISO with its offset. */
export function clockAt(cycleTs: string | null, minutes: number): string | null {
  if (!cycleTs) return null;
  const match = /T(\d{2}):(\d{2})/.exec(cycleTs);
  if (!match) return null;
  const total = Number(match[1]) * 60 + Number(match[2]) + minutes;
  const h = Math.floor((((total % 1440) + 1440) % 1440) / 60);
  const m = ((total % 60) + 60) % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** Legs in the order the water arrives: the earliest flood first, the calmest last. */
export function byFloodStart(legs: readonly PumpLeg[]): PumpLeg[] {
  return [...legs].sort(
    (a, b) =>
      (a.windowBefore?.fromMin ?? Infinity) - (b.windowBefore?.fromMin ?? Infinity) ||
      b.minutesSaved - a.minutesSaved,
  );
}

/** Each pump's place in the dispatch order: its index in the plan, as {@link routeLegs} uses. */
export function dispatchOrder(map: PumpMap | null): Map<string, number> {
  return new Map((map?.legs ?? []).map((leg, i) => [leg.pumpId, i]));
}

/** One line per assignment: the text alternative to the dispatch map. */
export function assignmentSentence(leg: PumpLeg, thresholdCm: number): string {
  const road = leg.road;
  const how = road
    ? `${Math.round(road.minutes)} min by road${road.distanceM != null ? ` over ${(road.distanceM / 1000).toFixed(1)} km` : ""}`
    : `${Math.round(leg.etaMin)} min in a straight line`;
  return (
    `${leg.pumpId} leaves ${leg.depot.name} for ${leg.target.name}, ${how}; ` +
    `${Math.round(leg.minutesBefore)} min above ${thresholdCm} cm with no pump, ` +
    `${Math.round(leg.minutesAfter)} min with it.`
  );
}
