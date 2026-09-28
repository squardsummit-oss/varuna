/**
 * What the camera frames: what is actually drawn, not the city's configured AOI.
 *
 * `/drains` draws the drain graph, the console draws the street network, and those cover different
 * ground. The AOI is the fallback for a map with nothing on it yet.
 */

import type { Bbox } from "../basemap";
import type { DrainPath, HotspotRing, RouteLine, SegmentPath } from "./types";

export interface DrawnExtentInput {
  routes: readonly RouteLine[];
  baseSegments: readonly SegmentPath[];
  segments: readonly SegmentPath[];
  drains: readonly DrainPath[];
  hotspots: readonly HotspotRing[];
  /** The area to frame when nothing is drawn, or the extent is too small to fit against. */
  fallback: Bbox;
}

export function drawnExtent({
  routes,
  baseSegments,
  segments,
  drains,
  hotspots,
  fallback,
}: DrawnExtentInput): Bbox {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  const eat = (lon: number, lat: number) => {
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) return;
    if (lon < west) west = lon;
    if (lon > east) east = lon;
    if (lat < south) south = lat;
    if (lat > north) north = lat;
  };
  // A drawn route wins: on `/route` the whole city is loaded for context, but the answer on
  // screen is one trip and the camera should be on it.
  for (const line of routes) for (const [lon, lat] of line.path) eat(lon, lat);
  if (Number.isFinite(west)) {
    // A little air around a route, which is a thin thing in a wide panel.
    const padLon = Math.max((east - west) * 0.35, 0.004);
    const padLat = Math.max((north - south) * 0.35, 0.004);
    return [
      [west - padLon, south - padLat],
      [east + padLon, north + padLat],
    ] as Bbox;
  }

  // Streets next: when the city layer is loaded it is the widest thing on the map, and it is
  // the extent the console should sit at.
  for (const segment of baseSegments) for (const [lon, lat] of segment.path) eat(lon, lat);
  if (!Number.isFinite(west)) {
    for (const segment of segments) for (const [lon, lat] of segment.path) eat(lon, lat);
  }
  if (!Number.isFinite(west)) {
    for (const drain of drains) for (const [lon, lat] of drain.path) eat(lon, lat);
  }
  if (!Number.isFinite(west)) for (const ring of hotspots) eat(ring.lon, ring.lat);
  // Nothing drawn, or an extent too small to fit against (one point, a single street).
  if (!Number.isFinite(west) || east - west < 1e-3 || north - south < 1e-3) return fallback;
  return [
    [west, south],
    [east, north],
  ] as Bbox;
}
