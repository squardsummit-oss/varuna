/**
 * What a screen can ask of `CityMap` without threading it through the map component between them.
 *
 * The console hosts `CityMap` inside `FloodMap`, which owns the run load and passes a fixed set of
 * props through. 3D mode, the console's routes layer and the what-if difference layer are three
 * things only the console asks for, so they arrive here, through context, rather than as three
 * more pass-through props on a component every other screen shares. Every screen that provides
 * nothing gets the defaults below and draws exactly what it drew before.
 */

import { createContext, useContext } from "react";

import type { Drains3dResult } from "./drains-3d";
import type { RouteLine } from "./types";

export interface MapOverlay {
  /**
   * Draw the city on Google's photorealistic 3D tiles, with every VARUNA layer draped on it
   * (SPEC.md 6.7, task P6.15). This asks for 3D; whether it happens is the tileset's answer,
   * which `CityMap` gets from `usePhotorealTileset` and nobody else has to thread through.
   */
  threeD?: boolean;
  /** Whose city: the slug the screen is showing. */
  city?: string;
  /**
   * The drain X-ray (motion M28): fade the photographed surface down and draw the inferred pipes
   * beneath it at their own invert elevations, so the network is walked through the streets it
   * runs under rather than looked at from above.
   */
  xray?: boolean;
  /**
   * How far the X-ray stretches the *cover* - how deep each pipe sits below its own street.
   * 1 is the real depth; anything else is labelled on screen by `exaggerationLabel`.
   */
  xrayExaggeration?: number;
  /**
   * What the X-ray actually managed to draw, reported back so the layer panel can print it.
   *
   * It is a callback rather than a return value because the counts depend on the camera, which
   * lives inside `CityMap` (`layers/camera.ts`): the zoom gates the manhole shafts and the
   * viewport selects the pipes. The console holds the result in state and prints
   * `drainXraySummary` of it; the identity is stable between camera moves (see `viewKey`), so
   * this costs one render per real change and none per frame.
   */
  onXray?: (result: Drains3dResult) => void;
  /** Routes to draw over the streets (SPEC.md 7.2, the "R" layer). */
  routes?: readonly RouteLine[];
  /**
   * A what-if answer to draw as the difference layer (SPEC.md 7.7): change in peak depth per
   * segment, and how far the M13 wipe has reached, 0 to 1.
   */
  diff?: { deltaCm: ReadonlyMap<string, number>; progress: number } | null;
}

const NONE: MapOverlay = {};

export const MapOverlayContext = createContext<MapOverlay>(NONE);

export function useMapOverlay(): MapOverlay {
  return useContext(MapOverlayContext);
}
