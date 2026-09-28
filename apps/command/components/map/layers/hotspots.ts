/**
 * The chronic hotspots as 120 m rings, with the rail's selection drawn brighter (SPEC.md 6.7,
 * motion M10).
 */

import { ScatterplotLayer } from "@deck.gl/layers";

import { HOTSPOT_RING, HOTSPOT_SELECTED } from "./palette";
import type { HotspotRing } from "./types";

export interface HotspotRingsLayerOptions {
  hotspots: readonly HotspotRing[];
  selectedHotspotId: string | null;
  show: boolean;
  /** Seam for motion M10's ring fade-in (chunk MO5), which never runs under reduced motion.
   * **Not read yet** - the selected ring appears at once. */
  reducedMotion?: boolean;
}

export function hotspotRingsLayers({
  hotspots,
  selectedHotspotId,
  show,
}: HotspotRingsLayerOptions): unknown[] {
  if (!show || hotspots.length === 0) return [];
  return [
    new ScatterplotLayer<HotspotRing>({
      id: "hotspot-rings",
      data: hotspots as HotspotRing[],
      getPosition: (d) => [d.lon, d.lat],
      // 120 m (section 6.7), so it reads as a place rather than a pin.
      getRadius: 120,
      radiusUnits: "meters",
      radiusMinPixels: 4,
      filled: false,
      stroked: true,
      // Unselected rings sit back; the selected one is the only glow on the map
      // (section 6.4), which is what makes the rail's click legible from a distance.
      getLineColor: (d) => (d.id === selectedHotspotId ? HOTSPOT_SELECTED : HOTSPOT_RING),
      getLineWidth: (d) => (d.id === selectedHotspotId ? 3 : 1.2),
      lineWidthUnits: "pixels",
      lineWidthMinPixels: 1,
      pickable: false,
      updateTriggers: {
        getLineColor: selectedHotspotId,
        getLineWidth: selectedHotspotId,
      },
    }),
  ];
}
