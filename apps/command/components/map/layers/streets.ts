/**
 * The streets this run wetted, coloured at the current step (SPEC.md 6.7, 6.2, 7.7, 7.11).
 *
 * One layer with four colourings, chosen in this order: the what-if diff, probability mode, the
 * public map's passability, and the operator's depth ramp. A scrub changes one thing, so one
 * accessor is re-run.
 */

import { PathLayer } from "@deck.gl/layers";

import { depthRgba, probabilityRgba } from "@/lib/ramps";
import { diffChanged, diffStreetColour } from "./diff";
import { STREET_HIGHLIGHT, passabilityRgba } from "./palette";
import type { SegmentPath } from "./types";

export interface WetStreetsLayerOptions {
  segments: readonly SegmentPath[];
  step: number;
  show: boolean;
  /** Draw by `deltaCm` rather than depth: the what-if diff layer. */
  diffMode: boolean;
  /** Longitude the diff wipe has reached; see `wipeLongitude`. */
  wipeLon: number;
  probabilityThresholdCm?: number;
  passableBelowCm?: number;
  /** Streets are pickable only when a screen listens for the pick. */
  pickable: boolean;
  /**
   * Seam for motion M7 (chunk MO5): the replay is playing, so colours may tween between steps.
   * **Not applied yet** - colours restyle instantly while playing too.
   */
  playing?: boolean;
  /** Seam for MO5: under reduced motion the M7 tween never runs. Not read yet. */
  reducedMotion?: boolean;
}

export function wetStreetsLayers({
  segments,
  step,
  show,
  diffMode,
  wipeLon,
  probabilityThresholdCm,
  passableBelowCm,
  pickable,
}: WetStreetsLayerOptions): unknown[] {
  if (!show || segments.length === 0) return [];
  return [
    new PathLayer<SegmentPath>({
      id: "streets-wet",
      data: segments as SegmentPath[],
      getPath: (d) => d.path,
      getColor: (d) => {
        // Motion M13: the diff wipes in left to right.
        if (diffMode) return diffStreetColour(d, wipeLon);
        const depth = d.depthCm[step] ?? 0;
        if (probabilityThresholdCm !== undefined) {
          // The measured fraction of members above the threshold when the run carries one
          // (SPEC.md 6.2: colour at the depth, opacity = P). Absent, **P is 0 or 1**, and
          // that is not an approximation: a run with no spread either puts the street over
          // the threshold or it does not. The 15 % floor keeps a below-threshold street
          // visible rather than vanishing, and the legend says which kind of run this is.
          const measured = d.pGt?.[String(probabilityThresholdCm)]?.[step];
          return probabilityRgba(depth, measured ?? (depth > probabilityThresholdCm ? 1 : 0));
        }
        return passableBelowCm === undefined
          ? depthRgba(depth)
          : passabilityRgba(depth, passableBelowCm);
      },
      getWidth: (d) =>
        // A changed street is drawn thicker, so the answer reads from across a room.
        diffMode && diffChanged(d) ? d.width * 1.8 : d.width * 1.15,
      widthUnits: "pixels",
      widthMinPixels: 1.6,
      capRounded: true,
      jointRounded: true,
      // Only when someone is listening: picking costs a second render pass, and the hero map
      // and the public map have nothing to do with a pick.
      pickable,
      // A line a few pixels wide is hard to hit; a 6 px tolerance is the difference between
      // "click the street" and "click exactly the street".
      autoHighlight: pickable,
      highlightColor: STREET_HIGHLIGHT,
      // A scrub changes one thing, so one accessor is re-run.
      updateTriggers: {
        getColor: [step, passableBelowCm, diffMode, wipeLon, probabilityThresholdCm],
        getWidth: [diffMode],
      },
    }),
  ];
}
