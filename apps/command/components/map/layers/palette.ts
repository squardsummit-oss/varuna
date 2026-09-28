/**
 * Every colour the map layers draw with, as deck.gl RGBA arrays, and the pure functions that pick
 * between them (SPEC.md 6.2).
 *
 * Arrays rather than CSS variables because deck.gl uploads colours to the GPU per instance and
 * cannot read a custom property. Each constant names the token it is, so a swatch in the legend
 * and a street on the map are provably the same colour.
 */

import type { RouteLine } from "./types";

export type Rgba = [number, number, number, number];

/** Section 6.7: the raster sits at 55 % so the streets read through it. */
export const RASTER_OPACITY = 0.55;

/** `--depth-dry` #2B3A55: present, and quiet enough that water is the only bright thing. */
export const DRY_STREET: Rgba = [43, 58, 85, 235];

/** `--deep` #111A2E, the panel colour: buildings are the ground the streets are cut into. */
export const BUILDING_FILL: Rgba = [17, 26, 46, 235];

/** `--line` #24314F: a hairline so a block reads as blocks rather than one grey mass. */
export const BUILDING_LINE: Rgba = [36, 49, 79, 170];

/** The public map's three colours (SPEC.md 7.11 and `PublicLegend`): go, slow down, do not
 * enter. A commuter does not need six depth bands, they need to know whether to turn around.
 *
 * `--depth-1` #3B82F6, `--depth-3` #F97316, `--depth-5` #B91C1C - the same three the legend on
 * that screen draws, so the swatch and the street are provably the same colour. */
export const PASSABLE: Rgba = [59, 130, 246, 235];
export const CAUTION: Rgba = [249, 115, 22, 245];
export const IMPASSABLE: Rgba = [185, 28, 28, 255];

/** Caution begins at this share of the vehicle's own stopping depth. */
export const CAUTION_FRACTION = 0.5;

export function passabilityRgba(depthCm: number, thresholdCm: number): Rgba {
  if (depthCm >= thresholdCm) return IMPASSABLE;
  if (depthCm >= thresholdCm * CAUTION_FRACTION) return CAUTION;
  return PASSABLE;
}

/** `--text` #E3EAF6 at 35 %: the hover highlight on a pickable street. */
export const STREET_HIGHLIGHT: Rgba = [227, 234, 246, 90];

/** `--naive` #64748B: the shortest path a navigation app would give you today. */
export const NAIVE_ROUTE: Rgba = [100, 116, 139, 235];

/** `--tide` #2DD4BF: the route VARUNA gives you instead. */
export const VARUNA_ROUTE: Rgba = [45, 212, 191, 255];

/** `--depth-5` #B91C1C: a street the route refused, so the detour has something to be around. */
export const AVOIDED_ROUTE: Rgba = [185, 28, 28, 255];

/** Every route line's colour, by what the line is. */
export const ROUTE_COLOUR: Record<RouteLine["kind"], Rgba> = {
  naive: NAIVE_ROUTE,
  varuna: VARUNA_ROUTE,
  alternate: [45, 212, 191, 150],
  avoided: AVOIDED_ROUTE,
};

/** `--ink` #0A1020: the casing that lifts the route off whatever it crosses (section 6.7). */
export const ROUTE_CASING: Rgba = [10, 16, 32, 235];

/** The what-if diff ramp (SPEC.md 7.7): blue improved, red worse, grey unchanged.
 *
 * `--depth-1` #3B82F6 for water removed and `--depth-4` #EF4444 for water added - the same two
 * ends of the depth ramp an operator already reads, so "blue is better" needs no legend. Grey is
 * `--depth-dry`, and it is deliberately the *majority* colour: most of a city does not change
 * when fourteen pipes are cleaned, and a diff layer that lights up everywhere is lying. */
export const DIFF_IMPROVED: [number, number, number] = [59, 130, 246];
export const DIFF_WORSE: [number, number, number] = [239, 68, 68];
export const DIFF_UNCHANGED: Rgba = [43, 58, 85, 190];

/** Change below this is not a change: the emulator's own noise floor is larger than half a cm. */
export const DIFF_DEADBAND_CM = 0.5;

/** Change at which the diff colour is fully saturated. Past 20 cm it is "a lot" either way. */
export const DIFF_FULL_CM = 20;

/** A segment's diff colour: opacity carries the size of the change, hue carries its sign. */
export function diffColour(deltaCm: number | undefined): Rgba {
  const delta = deltaCm ?? 0;
  if (Math.abs(delta) < DIFF_DEADBAND_CM) return DIFF_UNCHANGED;
  const strength = Math.min(Math.abs(delta) / DIFF_FULL_CM, 1);
  const [r, g, b] = delta < 0 ? DIFF_IMPROVED : DIFF_WORSE;
  return [r, g, b, Math.round(90 + 165 * strength)];
}

/** `--truth` #FFFFFF with a `--tide` ring: the sourced pins, and the only white on this map.
 *
 * White because they are the one thing here that is not a model output. Everything else on screen
 * is something VARUNA computed; these are what the city wrote down. */
export const TRUTH_FILL: Rgba = [255, 255, 255, 255];
export const TRUTH_RING: Rgba = [45, 212, 191, 255];

/** `--tide` #2DD4BF: the selected hotspot ring, the only glow on the map (section 6.4). */
export const HOTSPOT_SELECTED: Rgba = [45, 212, 191, 255];
/** `--tide` at half strength: the rings the rail has not selected. */
export const HOTSPOT_RING: Rgba = [45, 212, 191, 130];

/** `--surcharge` #EF4444, the manhole markers' outline. The fill's alpha is the pulse's. */
export const SURCHARGE_RGB: [number, number, number] = [239, 68, 68];
export const SURCHARGE_LINE: Rgba = [239, 68, 68, 240];

/** `--tide` at the section 6.2 opacities for the 5, 10 and 15-minute bands. */
export const REACH_FILL: Record<number, Rgba> = {
  5: [45, 212, 191, 115],
  10: [45, 212, 191, 71],
  15: [45, 212, 191, 36],
};

/** `--tide` at 55 %: the isochrone outline. */
export const REACH_LINE: Rgba = [45, 212, 191, 140];

/** The magenta blockage ramp of section 6.2, `--drain-0` through `--drain-3`. */
export const DRAIN_RAMP: [number, number, number][] = [
  [62, 76, 110],
  [124, 58, 237],
  [192, 38, 211],
  [232, 121, 249],
];

export function drainColour(beta: number): Rgba {
  const band = beta > 0.75 ? 3 : beta > 0.5 ? 2 : beta > 0.25 ? 1 : 0;
  const [r, g, b] = DRAIN_RAMP[band];
  return [r, g, b, 200];
}
