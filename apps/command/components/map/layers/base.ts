/**
 * The city's own ground: building footprints and the dry street network (SPEC.md 6.7).
 *
 * Neither changes with the scrub, which is why `CityMap` memoises them apart from the run's
 * layers - moving the time bar never rebuilds 39,259 building polygons.
 */

import { PathLayer, PolygonLayer } from "@deck.gl/layers";

import { BUILDING_FILL, BUILDING_LINE, DRY_STREET } from "./palette";
import type { BuildingPolygon, SegmentPath } from "./types";

export interface BuildingsLayerOptions {
  buildings: readonly BuildingPolygon[];
  show: boolean;
  /** 0 to 1 opacity, for the onboarding wizard's layer stack (motion M19). 1 everywhere else. */
  fade?: number;
}

export function buildingsLayers({ buildings, show, fade = 1 }: BuildingsLayerOptions): unknown[] {
  if (!show || buildings.length === 0 || fade <= 0) return [];
  return [
    new PolygonLayer<BuildingPolygon>({
      id: "buildings",
      // Only when it is fading: the equivalence fixtures record the props deck is handed, and a
      // layer at full opacity must hand it exactly what it did before M19 existed.
      ...(fade < 1 ? { opacity: fade } : {}),
      data: buildings as BuildingPolygon[],
      getPolygon: (d) => d,
      filled: true,
      getFillColor: BUILDING_FILL,
      stroked: true,
      getLineColor: BUILDING_LINE,
      lineWidthMinPixels: 0.4,
      lineWidthUnits: "pixels",
      pickable: false,
    }),
  ];
}

export interface DryStreetsLayerOptions {
  baseSegments: readonly SegmentPath[];
  /** 0 to 1 opacity, for the onboarding wizard's layer stack (motion M19). 1 everywhere else. */
  fade?: number;
}

/**
 * The whole street network, dim. This is the geography the operator orients by, and it is the
 * same 21,296 segments the city pipeline derived - not a tile service's idea of Mumbai.
 */
export function dryStreetsLayers({ baseSegments, fade = 1 }: DryStreetsLayerOptions): unknown[] {
  if (baseSegments.length === 0 || fade <= 0) return [];
  return [
    new PathLayer<SegmentPath>({
      id: "streets-dry",
      ...(fade < 1 ? { opacity: fade } : {}),
      data: baseSegments as SegmentPath[],
      getPath: (d) => d.path,
      getColor: DRY_STREET,
      getWidth: (d) => Math.max(d.width * 0.7, 0.8),
      widthUnits: "pixels",
      widthMinPixels: 0.6,
      capRounded: true,
      jointRounded: true,
      pickable: false,
    }),
  ];
}
