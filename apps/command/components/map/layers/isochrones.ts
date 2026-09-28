/**
 * Reachability bands as translucent polygons, under the routes and over the streets
 * (SPEC.md 6.7, 7.4, motion M15).
 */

import { PolygonLayer } from "@deck.gl/layers";
import { useMemo } from "react";

import { DUR_MS, easeUi } from "@/lib/motion";

import { REACH_FILL, REACH_LINE } from "./palette";
import type { Isochrone } from "./types";

/**
 * Slices handed back by {@link useDisplayedIsochrones} under reduced motion.
 *
 * `CityMap` calls `isochroneLayers({ isochrones })` with the hook's return value and nothing
 * else, so the reduced-motion answer has to travel with the slice itself. A `WeakSet` of the
 * arrays that must swap instantly is that channel: it adds no field to `Isochrone`, keeps the
 * serialised layers of an animated slice exactly what they were, and lets go of a slice as soon
 * as the map does.
 */
const INSTANT = new WeakSet<readonly Isochrone[]>();

/**
 * The deck.gl transitions for motion M15: the polygons morph to a new slice over 300 ms on the
 * catalogue's UI easing (SPEC.md 8), and their fill with them, so a band that shrinks does so
 * as one movement rather than as a jump followed by a colour change.
 *
 * `getPolygon` is interpolated by deck itself. Its polygon layers pad a vertex buffer of a
 * different length from the previous one's last vertex (`ATTRIBUTE_TRANSITION.enter` in
 * `solid-polygon-layer`), so a hull that gained or lost vertices between slices still tweens
 * rather than snapping. The outline follows: `PolygonLayer` forwards `getPolygon` to its path
 * sub-layer as `getPath`.
 */
export const ISOCHRONE_TRANSITIONS = {
  getPolygon: { duration: DUR_MS.isochroneMorph, easing: easeUi },
  getFillColor: { duration: DUR_MS.isochroneMorph, easing: easeUi },
} as const;

/**
 * The bands to draw this frame (motion M15).
 *
 * The morph itself runs on the GPU through {@link ISOCHRONE_TRANSITIONS}, so there is nothing to
 * hold here in React and no render per frame. What this hook decides is whether the next slice
 * may tween at all: with motion on it returns its input untouched; under reduced motion it
 * returns a copy marked to swap instantly, which is the catalogue's fallback ("instant").
 */
export function useDisplayedIsochrones(
  isochrones: readonly Isochrone[],
  reducedMotion: boolean,
): readonly Isochrone[] {
  return useMemo(() => {
    if (!reducedMotion) return isochrones;
    const instant = [...isochrones];
    INSTANT.add(instant);
    return instant;
  }, [isochrones, reducedMotion]);
}

export interface IsochroneLayerOptions {
  isochrones: readonly Isochrone[];
  /** Swap a new slice in at once. Defaults to whatever {@link useDisplayedIsochrones} decided. */
  reducedMotion?: boolean;
}

/** Whether a slice will tween when it replaces the one on screen. */
export function isochronesMorph(
  isochrones: readonly Isochrone[],
  reducedMotion?: boolean,
): boolean {
  return !(reducedMotion ?? INSTANT.has(isochrones));
}

export function isochroneLayers({ isochrones, reducedMotion }: IsochroneLayerOptions): unknown[] {
  if (isochrones.length === 0) return [];
  const morph = isochronesMorph(isochrones, reducedMotion);
  return [
    new PolygonLayer<Isochrone>({
      id: "isochrones",
      // Largest band first, so the 5-minute core reads as the darkest patch rather than
      // being painted over by the 15-minute one. The order is by band, never by the slice, so
      // the 15-minute polygon of one slice tweens into the 15-minute polygon of the next.
      data: [...isochrones].sort((a, b) => b.minutes - a.minutes) as Isochrone[],
      getPolygon: (d) => d.rings[0] ?? [],
      getFillColor: (d) => REACH_FILL[d.minutes] ?? REACH_FILL[15],
      getLineColor: REACH_LINE,
      getLineWidth: 1,
      lineWidthUnits: "pixels",
      stroked: true,
      filled: true,
      pickable: false,
      ...(morph ? { transitions: ISOCHRONE_TRANSITIONS } : {}),
    }),
  ];
}
