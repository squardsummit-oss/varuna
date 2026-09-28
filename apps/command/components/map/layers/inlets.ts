/**
 * Drain inlets as small squares coloured by clogging κ (SPEC.md 7.3).
 *
 * **A seam, not a layer yet.** Pulse does not assimilate κ (task P7.3) and chunk PU8 draws the
 * inlets; until then this returns nothing, and `/drains` says so in words beside the map rather
 * than drawing a legend for squares that are not there.
 */

import type { InletPoint } from "./types";

export interface InletLayerOptions {
  inlets: readonly InletPoint[];
  show: boolean;
}

export function inletLayers(options: InletLayerOptions): unknown[] {
  void options;
  return [];
}
