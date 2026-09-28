/**
 * The depth raster for the current step (SPEC.md 6.7): one pre-decoded frame, so a scrub swaps a
 * texture and never fetches or decodes (7.2: "no network during scrub").
 */

import { BitmapLayer } from "@deck.gl/layers";

import { RASTER_OPACITY } from "./palette";

export interface DepthRasterLayerOptions {
  frame: ImageBitmap | null;
  bounds: [number, number, number, number] | null;
  show: boolean;
  /** 0 to 1 multiplier on the raster's own opacity (motion M19). 1 everywhere else. */
  fade?: number;
}

export function depthRasterLayers({
  frame,
  bounds,
  show,
  fade = 1,
}: DepthRasterLayerOptions): unknown[] {
  if (!show || !frame || !bounds || fade <= 0) return [];
  return [
    new BitmapLayer({
      id: "depth-raster",
      bounds,
      image: frame,
      opacity: RASTER_OPACITY * fade,
      pickable: false,
      // Nearest on magnify: a 30 m cell is a real measurement and smoothing it across the
      // screen would imply a resolution the model does not have.
      textureParameters: { minFilter: "linear", magFilter: "nearest" },
    }),
  ];
}
