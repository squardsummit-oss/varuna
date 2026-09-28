/**
 * The what-if diff layer's wipe (SPEC.md 7.7, motion M13): where the reveal has reached, and
 * what a street on either side of it is drawn as.
 */

import type { Bbox } from "../basemap";
import { DIFF_DEADBAND_CM, DIFF_UNCHANGED, diffColour, type Rgba } from "./palette";
import type { SegmentPath } from "./types";

/**
 * Where the diff wipe has reached, as a longitude. Derived from the drawn extent rather than from
 * a screen-space mask, so the wipe follows the city and not the window. Infinity when there is no
 * wipe in progress, so every street is east of nothing.
 */
export function wipeLongitude(diffMode: boolean, diffProgress: number, frame: Bbox): number {
  if (!diffMode || diffProgress >= 1) return Number.POSITIVE_INFINITY;
  const [[west], [east]] = frame;
  return west + (east - west) * diffProgress;
}

/**
 * A street's diff colour. A segment east of the wipe is drawn unchanged rather than hidden, so the
 * network stays whole while the answer arrives - hiding it would read as "these streets were
 * deleted".
 */
export function diffStreetColour(segment: SegmentPath, wipeLon: number): Rgba {
  const lon = segment.path[0]?.[0] ?? 0;
  return lon <= wipeLon ? diffColour(segment.deltaCm) : DIFF_UNCHANGED;
}

/** Whether the what-if moved this street by more than the emulator's noise floor. */
export function diffChanged(segment: SegmentPath): boolean {
  return Math.abs(segment.deltaCm ?? 0) >= DIFF_DEADBAND_CM;
}
