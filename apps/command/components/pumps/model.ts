/**
 * Pure helpers behind Jalayantra's dispatch view: nothing here renders, so everything here is
 * tested without a map or a DOM.
 */

import type { Bbox } from "@/components/map/basemap";
import type { PumpRouteLeg, UnservedPlace } from "@/components/map/layers/pump-routes";
import type { PumpLeg, PumpMap } from "@/lib/api/pumps";

/** Degrees added around the depots and places so a ring at the edge is not cut by the frame. */
const FRAME_PAD_DEG = 0.006;

/** The frame that holds every depot and every place a pump goes: the view opens on the plan. */
export function dispatchBounds(map: PumpMap | null): Bbox | undefined {
  if (!map) return undefined;
  const points: [number, number][] = [];
  for (const leg of map.legs) {
    if (leg.depot.lon != null && leg.depot.lat != null) points.push([leg.depot.lon, leg.depot.lat]);
    if (leg.target.lon != null && leg.target.lat != null)
      points.push([leg.target.lon, leg.target.lat]);
  }
  if (points.length === 0) return undefined;
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  return [
    [Math.min(...lons) - FRAME_PAD_DEG, Math.min(...lats) - FRAME_PAD_DEG],
    [Math.max(...lons) + FRAME_PAD_DEG, Math.max(...lats) + FRAME_PAD_DEG],
  ];
}

/**
 * The legs the map can draw: both ends known. The road when there is one, else the line. `order`
 * is the leg's place in the plan, which is the order the lorries leave in (M33) and the order the
 * gauges drain in (M34), so the map and the gauges time from the same index.
 */
export function routeLegs(map: PumpMap | null): PumpRouteLeg[] {
  if (!map) return [];
  const out: PumpRouteLeg[] = [];
  for (const [order, leg] of map.legs.entries()) {
    const { depot, target } = leg;
    if (depot.lon == null || depot.lat == null || target.lon == null || target.lat == null) {
      continue;
    }
    const from: [number, number] = [depot.lon, depot.lat];
    const to: [number, number] = [target.lon, target.lat];
    const road = leg.road && leg.road.path.length > 1 ? leg.road.path : null;
    out.push({
      pumpId: leg.pumpId,
      depot: from,
      target: to,
      targetName: leg.target.name,
      depotName: depot.name,
      path: road ?? [from, to],
      routed: road !== null,
      peakBeforeCm: leg.peakBefore?.depthCm ?? 0,
      peakAfterCm: leg.peakAfter?.depthCm ?? 0,
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
  kind: "early" | "late" | "dry";
  /** Minutes between arrival and the water crossing 45 cm; always positive. */
  marginMin: number;
}

/**
 * The race the timeline draws: the plan's ETA against the first forecast minute above 45 cm
 * without a pump. "dry" when the place never crosses (the plan would not have sent a pump).
 */
export function race(leg: PumpLeg): Race {
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
