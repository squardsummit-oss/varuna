/** Shared types for `CityMap` and the WebGL canvas it loads (SPEC.md task P6.1). */
import type { Layer, PickingInfo } from "@deck.gl/core";

/**
 * How much of the map the audience may touch (SPEC.md sections 6.7, 7.1 and 7.11):
 * - `console` — the operator's map: drag, zoom, rotate, keyboard.
 * - `hero`    — the landing embed: read-only, no cursor affordance, no keyboard trap.
 * - `public`  — the citizen map: pan and zoom by touch, no rotation or pitch to get lost in.
 */
export type CityMapMode = "console" | "hero" | "public";

/** The camera, in the units the time bar and the hotspot rail already speak. */
export interface CityMapViewState {
  longitude: number;
  latitude: number;
  zoom: number;
  bearing: number;
  pitch: number;
}

/** Where a caller wants the camera; anything omitted is left where it is. */
export interface FlyToTarget {
  longitude: number;
  latitude: number;
  zoom?: number;
  /** Override the 900 ms of motion M10. Ignored under reduced motion, which always jump-cuts. */
  durationMs?: number;
}

/** Imperative handle on the map, for the hotspot fly-to and for tests. */
export interface CityMapHandle {
  /**
   * Motion M10: a 900 ms flight to the target, or a jump cut when the operator asks for reduced
   * motion. No-op while the map is loading or recovering from a lost WebGL context.
   */
  flyTo: (target: FlyToTarget) => void;
  /** Re-fit the current area of interest. */
  fitBounds: () => void;
  /** The camera now, or null before the map has loaded. */
  getViewState: () => CityMapViewState | null;
}

/** deck.gl layers the map renders, interleaved into the basemap's own layer stack. */
export type DeckLayers = readonly Layer[];

export type { PickingInfo };
