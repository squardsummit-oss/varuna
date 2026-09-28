/**
 * Where a map should open: on the water, not on the whole city (the "fit by default" request).
 *
 * Every map used to open on the city's AOI - 9.5 by 15.5 km for Mumbai - so the streets that were
 * actually flooding were a few pixels each. This module picks a smaller frame from the run's own
 * numbers, and nothing else: no coordinates are written down here.
 *
 * **The rule, in one sentence:** the frame is the square of at most `maxSpanM` (7 km) that holds
 * the most qualifying street length, trimmed of the odd far street inside it, grown to hold any
 * point the caller names in `include` (the console passes the five chronic spots its rail lists
 * first), padded, and never narrower than `minSpanM` (1.2 km) so it cannot zoom onto one street.
 *
 * Why a window and not the extent of everything wet: measured on the 2 July 2019 runs on
 * 2026-09-28, the streets at or above 15 cm at the 08:40 cycle's peak step are 118.5 km spread
 * over the whole AOI (every 1 km cell from Colaba's edge to Mahim holds some), so their extent *is*
 * the AOI and framing it changes nothing. A flood that is compact fits inside the window whole and
 * is framed whole; a flood that is everywhere is framed where it is densest, and the screen says
 * what share of it that is. `WHOLE` is the other answer - every qualifying street, no window -
 * and it is what the console's full view asks for.
 *
 * Why 7 km: on the same runs a 6 km window held 68 % (06:40) and 54 % (08:40) of the deep
 * length, a 7 km one 85 % and 70 %, an 8 km one 88 % and 78 %. Seven is where the gain per
 * kilometre falls off.
 *
 * Zooms, measured with deck's own `WebMercatorViewport.fitBounds` (512 px tiles) at the console
 * map's 1080 x 752 (a 1440 x 900 window) with the console's padding clear of its layer column and
 * scrub card: the 7 km window alone opens at 12.29 (06:40) and 12.30 (08:40). With the rail's top
 * five held, 06:40 stays at 12.29 and 08:40 opens on the AOI's full height at 11.42, because its
 * top five run from Hindmata to Kurla LBS Marg, 8 km apart. The AOI with a 12 px margin is 11.75.
 * Every one of these sits **below** the zoom at which names draw (15 for streets, 13.5 for
 * stations and fire stations, `components/map/labels.tsx`): the opening frame is an overview of
 * where the water is, and a street is read by zooming in, from its popover or from the rail.
 *
 * Why length and not a count: a 400 m arterial under water matters more than a 20 m service lane,
 * and "most of the deep length" is the thing a reader can check against the map.
 *
 * The fallbacks, in order: streets at 15 cm or more at the run's peak step, then at 5 cm or more,
 * then the run's chronic spots, then the AOI. The result names which one it used.
 */

import { useState } from "react";

import type { Bbox } from "@/components/map/basemap";

/** A lon/lat pair. */
export type LonLat = readonly [number, number];

/** A point with a weight: metres of street, a pipe's share of learning, a manhole's discharge. */
export interface WeightedPoint {
  lon: number;
  lat: number;
  weight: number;
}

export interface FrameOptions {
  /** The densest window's side, in metres. A frame is never wider than this before padding. */
  maxSpanM?: number;
  /** The narrowest a frame may be, in metres, so it never zooms onto one street. */
  minSpanM?: number;
  /** The density grid's cell, in metres. */
  cellM?: number;
  /** Weighted share trimmed from each side inside the window, so one far street cannot stretch it. */
  trim?: number;
  /** Padding as a share of the span on each side, never less than `minPadM`. */
  padFraction?: number;
  minPadM?: number;
  /** Keep the frame inside this box (the city's AOI): shifted in, and shrunk if it is larger. */
  within?: Bbox;
  /**
   * Points the frame must hold whatever the window chose: grown to reach them before padding.
   * The console passes the chronic spots at the top of its rail, so the map and the rail agree.
   */
  include?: readonly Pick<WeightedPoint, "lon" | "lat">[];
}

const DEFAULTS = {
  maxSpanM: 7_000,
  minSpanM: 1_200,
  cellM: 250,
  trim: 0.02,
  padFraction: 0.12,
  minPadM: 120,
} as const;

/**
 * Every qualifying street, with no window and no trim: the whole affected picture. What the
 * console's full view frames (the user's "show the full map to easily understand the affected
 * areas"), so its caption can say it holds all of the water rather than the densest part.
 */
export const WHOLE: FrameOptions = { maxSpanM: Number.MAX_SAFE_INTEGER, trim: 0 };

/** Metres per degree of latitude, and of longitude at the equator. */
const M_PER_DEG_LAT = 110_540;
const M_PER_DEG_LON = 111_320;

/** More cells than this and the grid coarsens: a frame is a view, not a survey. */
const MAX_GRID_CELLS = 250_000;

/** A framed area and how much of what qualified it holds. */
export interface DenseFrame {
  bounds: Bbox;
  /** Share of the total weight inside `bounds`, 0 to 1. */
  share: number;
  /** Total weight of every point offered, inside the frame or not. */
  total: number;
}

/** Length of a lon/lat path in metres, on a local flat earth (street and pipe scale). */
export function pathLengthM(path: readonly LonLat[]): number {
  let total = 0;
  for (let i = 1; i < path.length; i += 1) {
    const [lon0, lat0] = path[i - 1]!;
    const [lon1, lat1] = path[i]!;
    const kx = M_PER_DEG_LON * Math.cos((((lat0 + lat1) / 2) * Math.PI) / 180);
    total += Math.hypot((lon1 - lon0) * kx, (lat1 - lat0) * M_PER_DEG_LAT);
  }
  return Number.isFinite(total) ? total : 0;
}

/**
 * A path as weighted points: every vertex, sharing `weight` equally, so a trimmed box still
 * reaches both ends of a street rather than stopping at its midpoint.
 */
export function pathPoints(path: readonly LonLat[], weight: number): WeightedPoint[] {
  if (path.length === 0 || !(weight > 0)) return [];
  const each = weight / path.length;
  return path.map(([lon, lat]) => ({ lon, lat, weight: each }));
}

/** Weighted quantile of `values` (with `weights`), both sorted together by value. */
function weightedQuantile(values: number[], weights: number[], q: number): number {
  const order = values.map((_, i) => i).sort((a, b) => values[a]! - values[b]! || a - b);
  const total = weights.reduce((sum, w) => sum + w, 0);
  const target = q * total;
  let cumulative = 0;
  for (const i of order) {
    cumulative += weights[i]!;
    if (cumulative >= target) return values[i]!;
  }
  return values[order[order.length - 1]!]!;
}

interface Projected {
  x: number;
  y: number;
  w: number;
}

/**
 * The frame that holds the most weight within `maxSpanM`, trimmed, padded and clamped. Null when
 * no point carries a positive weight.
 *
 * Deterministic: the window search scans every position on the grid and breaks a tie towards the
 * weighted centre, then towards the south-west, so the same run always opens on the same frame.
 */
export function denseFrame(
  points: readonly WeightedPoint[],
  options: FrameOptions = {},
): DenseFrame | null {
  const o = { ...DEFAULTS, ...options };
  const kept = points.filter(
    (p) => Number.isFinite(p.lon) && Number.isFinite(p.lat) && p.weight > 0,
  );
  if (kept.length === 0) return null;

  const total = kept.reduce((sum, p) => sum + p.weight, 0);
  const lat0 = kept.reduce((sum, p) => sum + p.lat * p.weight, 0) / total;
  const lon0 = kept.reduce((sum, p) => sum + p.lon * p.weight, 0) / total;
  const kx = M_PER_DEG_LON * Math.cos((lat0 * Math.PI) / 180);
  const ky = M_PER_DEG_LAT;
  const pts: Projected[] = kept.map((p) => ({
    x: (p.lon - lon0) * kx,
    y: (p.lat - lat0) * ky,
    w: p.weight,
  }));

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of pts) {
    if (p.x < minX) minX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.x > maxX) maxX = p.x;
    if (p.y > maxY) maxY = p.y;
  }

  // The grid. Coarsened, never truncated, if the points are spread wider than a city.
  let cell = o.cellM;
  const cellsFor = (c: number) =>
    (Math.floor((maxX - minX) / c) + 1) * (Math.floor((maxY - minY) / c) + 1);
  while (cellsFor(cell) > MAX_GRID_CELLS) cell *= 2;
  const nx = Math.floor((maxX - minX) / cell) + 1;
  const ny = Math.floor((maxY - minY) / cell) + 1;
  const ix = (x: number) => Math.min(nx - 1, Math.floor((x - minX) / cell));
  const iy = (y: number) => Math.min(ny - 1, Math.floor((y - minY) / cell));

  const grid = new Float64Array(nx * ny);
  for (const p of pts) {
    const k = iy(p.y) * nx + ix(p.x);
    grid[k] = (grid[k] ?? 0) + p.w;
  }
  // Summed-area table, one row and column larger, so any window's weight is four reads.
  const sat = new Float64Array((nx + 1) * (ny + 1));
  for (let y = 0; y < ny; y += 1) {
    let row = 0;
    for (let x = 0; x < nx; x += 1) {
      row += grid[y * nx + x]!;
      sat[(y + 1) * (nx + 1) + (x + 1)] = sat[y * (nx + 1) + (x + 1)]! + row;
    }
  }
  const windowWeight = (x0: number, y0: number, wx: number, wy: number) =>
    sat[(y0 + wy) * (nx + 1) + (x0 + wx)]! -
    sat[y0 * (nx + 1) + (x0 + wx)]! -
    sat[(y0 + wy) * (nx + 1) + x0]! +
    sat[y0 * (nx + 1) + x0]!;

  const side = Math.max(1, Math.round(o.maxSpanM / cell));
  const wx = Math.min(side, nx);
  const wy = Math.min(side, ny);
  // The weighted centre, in cells, for the tie-break.
  const cx = -minX / cell;
  const cy = -minY / cell;
  let best = { x0: 0, y0: 0, weight: -Infinity, dist: Infinity };
  for (let y0 = 0; y0 + wy <= ny; y0 += 1) {
    for (let x0 = 0; x0 + wx <= nx; x0 += 1) {
      const weight = windowWeight(x0, y0, wx, wy);
      const dist = Math.hypot(x0 + wx / 2 - cx, y0 + wy / 2 - cy);
      // A relative tolerance, so float noise in the sums cannot decide between equal windows.
      const better =
        weight > best.weight * (1 + 1e-9) ||
        (Math.abs(weight - best.weight) <= Math.abs(best.weight) * 1e-9 && dist < best.dist);
      if (better) best = { x0, y0, weight, dist };
    }
  }

  const inside = pts.filter((p) => {
    const px = ix(p.x);
    const py = iy(p.y);
    return px >= best.x0 && px < best.x0 + wx && py >= best.y0 && py < best.y0 + wy;
  });
  const xs = inside.map((p) => p.x);
  const ys = inside.map((p) => p.y);
  const ws = inside.map((p) => p.w);
  let x0 = weightedQuantile(xs, ws, o.trim);
  let x1 = weightedQuantile(xs, ws, 1 - o.trim);
  let y0 = weightedQuantile(ys, ws, o.trim);
  let y1 = weightedQuantile(ys, ws, 1 - o.trim);

  // Grown to hold what the caller named, before the padding so those points are not on the edge.
  for (const point of options.include ?? []) {
    if (!Number.isFinite(point.lon) || !Number.isFinite(point.lat)) continue;
    const x = (point.lon - lon0) * kx;
    const y = (point.lat - lat0) * ky;
    x0 = Math.min(x0, x);
    x1 = Math.max(x1, x);
    y0 = Math.min(y0, y);
    y1 = Math.max(y1, y);
  }

  // Pad, then widen to the minimum span about the same centre.
  const padX = Math.max((x1 - x0) * o.padFraction, o.minPadM);
  const padY = Math.max((y1 - y0) * o.padFraction, o.minPadM);
  x0 -= padX;
  x1 += padX;
  y0 -= padY;
  y1 += padY;
  if (x1 - x0 < o.minSpanM) {
    const mid = (x0 + x1) / 2;
    x0 = mid - o.minSpanM / 2;
    x1 = mid + o.minSpanM / 2;
  }
  if (y1 - y0 < o.minSpanM) {
    const mid = (y0 + y1) / 2;
    y0 = mid - o.minSpanM / 2;
    y1 = mid + o.minSpanM / 2;
  }

  let bounds: Bbox = [
    [lon0 + x0 / kx, lat0 + y0 / ky],
    [lon0 + x1 / kx, lat0 + y1 / ky],
  ];
  if (o.within) bounds = clampInside(bounds, o.within);

  const [[west, south], [east, north]] = bounds;
  const held = kept
    .filter((p) => p.lon >= west && p.lon <= east && p.lat >= south && p.lat <= north)
    .reduce((sum, p) => sum + p.weight, 0);
  return { bounds, share: Math.min(1, held / total), total };
}

/**
 * `box` moved inside `outer` where it overflows, and cut to `outer` on any side it is larger than.
 * A frame outside the city would open on sea or on nothing VARUNA computed.
 */
export function clampInside(box: Bbox, outer: Bbox): Bbox {
  const [[w, s], [e, n]] = box;
  const [[ow, os], [oe, on]] = outer;
  const fit = (lo: number, hi: number, olo: number, ohi: number): [number, number] => {
    const span = hi - lo;
    if (span >= ohi - olo) return [olo, ohi];
    if (lo < olo) return [olo, olo + span];
    if (hi > ohi) return [ohi - span, ohi];
    return [lo, hi];
  };
  const [west, east] = fit(w, e, ow, oe);
  const [south, north] = fit(s, n, os, on);
  return [
    [west, south],
    [east, north],
  ];
}

/**
 * The frame around every vertex of `paths` - a route, its alternates and the streets it avoided -
 * padded and never narrower than `minSpanM`. No window and no trimming: a route that runs off the
 * edge of its own frame is a route the reader cannot follow. Null when there is nothing to frame.
 */
export function pathsFrame(
  paths: readonly (readonly LonLat[])[],
  options: Pick<FrameOptions, "minSpanM" | "padFraction" | "minPadM" | "within"> = {},
): Bbox | null {
  const points = paths.flatMap((path) => path.map(([lon, lat]) => ({ lon, lat, weight: 1 })));
  return extentFrame(points, options);
}

/** The same, for points: an origin and a destination, a set of chronic spots. */
export function extentFrame(
  points: readonly Pick<WeightedPoint, "lon" | "lat">[],
  options: Pick<FrameOptions, "minSpanM" | "padFraction" | "minPadM" | "within"> = {},
): Bbox | null {
  const finite = points.filter((p) => Number.isFinite(p.lon) && Number.isFinite(p.lat));
  if (finite.length === 0) return null;
  // The whole extent is the window: a span larger than any city and no trim.
  return (
    denseFrame(
      finite.map((p) => ({ lon: p.lon, lat: p.lat, weight: 1 })),
      { ...options, maxSpanM: Number.MAX_SAFE_INTEGER, trim: 0, cellM: 1_000 },
    )?.bounds ?? null
  );
}

/** A street as this module reads it: its line and its depth at each of the run's steps. */
export interface DepthStreet {
  path: readonly LonLat[];
  depthCm: readonly number[];
}

/** Which evidence a frame was drawn from, most specific first. */
export type AffectedBasis = "deep" | "wet" | "hotspots" | "aoi";

export interface AffectedFrame {
  bounds: Bbox;
  basis: AffectedBasis;
  /** The depth a street had to reach to count, or null for the hotspot and AOI fallbacks. */
  thresholdCm: number | null;
  /** The step the streets were read at, or null for the fallbacks. */
  step: number | null;
  /** Share of the qualifying street length inside the frame, 0 to 1 (1 for the AOI). */
  share: number;
  /** All the qualifying street length, in metres, framed or not. */
  lengthM: number;
}

/**
 * The step at which the most street length stands at or above `thresholdCm`, with that length.
 * Ties go to the earliest step. Null when no street reaches it at any step.
 */
export function peakStep(
  streets: readonly DepthStreet[],
  thresholdCm: number,
  lengths: readonly number[] = streets.map((s) => pathLengthM(s.path)),
): { step: number; lengthM: number } | null {
  const byStep: number[] = [];
  streets.forEach((street, i) => {
    const len = lengths[i] ?? 0;
    if (!(len > 0)) return;
    street.depthCm.forEach((d, step) => {
      if (d >= thresholdCm) byStep[step] = (byStep[step] ?? 0) + len;
    });
  });
  let best: { step: number; lengthM: number } | null = null;
  byStep.forEach((len, step) => {
    if (len > 0 && (!best || len > best.lengthM)) best = { step, lengthM: len };
  });
  return best;
}

/** Less qualifying street length than this, in metres, is not an area to frame. */
export const MIN_AREA_M = 200;

export interface AffectedInput extends FrameOptions {
  streets: readonly DepthStreet[];
  /** Read the streets at this step; absent, at each threshold's own peak step. */
  step?: number;
  /** Depths to try, deepest first. */
  thresholdsCm?: readonly number[];
  /** Less qualifying street length than this is not an area; the next threshold is tried. */
  minLengthM?: number;
  /** The run's chronic spots, framed when no street qualifies. */
  hotspots?: readonly Pick<WeightedPoint, "lon" | "lat">[];
  /** The city's AOI: the last resort, and the box every frame is kept inside. */
  fallback: Bbox;
}

/**
 * The main affected area of a run: see the module comment for the rule. Always returns a frame;
 * `basis` says whether it came from the water, the chronic spots or the city's own box.
 */
export function affectedFrame({
  streets,
  step,
  thresholdsCm = [15, 5],
  minLengthM = MIN_AREA_M,
  hotspots = [],
  fallback,
  ...options
}: AffectedInput): AffectedFrame {
  const within = options.within ?? fallback;
  const lengths = streets.map((s) => pathLengthM(s.path));
  for (const [i, thresholdCm] of thresholdsCm.entries()) {
    const at = step ?? peakStep(streets, thresholdCm, lengths)?.step;
    if (at === undefined) continue;
    const points: WeightedPoint[] = [];
    let lengthM = 0;
    streets.forEach((street, k) => {
      const depth = street.depthCm[at];
      const len = lengths[k] ?? 0;
      if (depth === undefined || !(depth >= thresholdCm) || !(len > 0)) return;
      lengthM += len;
      points.push(...pathPoints(street.path, len));
    });
    if (lengthM < minLengthM) continue;
    const frame = denseFrame(points, { ...options, within });
    if (!frame) continue;
    return {
      bounds: frame.bounds,
      basis: i === 0 ? "deep" : "wet",
      thresholdCm,
      step: at,
      share: frame.share,
      lengthM,
    };
  }
  const spots = extentFrame(hotspots, { ...options, within });
  if (spots) {
    return {
      bounds: spots,
      basis: "hotspots",
      thresholdCm: null,
      step: null,
      share: 1,
      lengthM: 0,
    };
  }
  return { bounds: fallback, basis: "aoi", thresholdCm: null, step: null, share: 1, lengthM: 0 };
}

/**
 * The affected frame at `step`, or at the run's peak when `step` has too little water to frame:
 * the console's full view, entered at the step on screen. Framed at 06:45 on the 06:40 cycle, the
 * storm has not reached any street yet, and the chronic spots (what `affectedFrame` falls back to)
 * would say nothing about where the water is going; the peak does. With no `step`, the peak.
 */
export function affectedFrameAt(
  input: Omit<AffectedInput, "step">,
  step: number | null | undefined,
): AffectedFrame {
  if (step === null || step === undefined) return affectedFrame(input);
  const at = affectedFrame({ ...input, step });
  return at.basis === "deep" || at.basis === "wet" ? at : affectedFrame(input);
}

/** Intersection over union of two boxes, 0 when either is missing or they do not overlap. */
export function boundsOverlap(a: Bbox | null, b: Bbox | null): number {
  if (!a || !b) return 0;
  const w = Math.max(a[0][0], b[0][0]);
  const s = Math.max(a[0][1], b[0][1]);
  const e = Math.min(a[1][0], b[1][0]);
  const n = Math.min(a[1][1], b[1][1]);
  const area = (box: Bbox) => (box[1][0] - box[0][0]) * (box[1][1] - box[0][1]);
  const inter = e > w && n > s ? (e - w) * (n - s) : 0;
  const union = area(a) + area(b) - inter;
  return union > 0 ? inter / union : 0;
}

/**
 * `next`, unless the frame already held is close enough to it (overlap at least `keepAbove`).
 *
 * For a frame read at a scrubbed step, which moves a little with every step: the camera re-frames
 * when the water has gone somewhere else, not each time a few streets cross the line. Set during
 * render (React's pattern for state derived from a prop), so no frame paints at the old box.
 */
export function useSettledBounds(next: Bbox | null, keepAbove = 0.6): Bbox | null {
  const [held, setHeld] = useState<Bbox | null>(next);
  const same =
    held === next || (held !== null && next !== null && boundsOverlap(held, next) >= keepAbove);
  if (!same) {
    setHeld(next);
    return next;
  }
  return held;
}
