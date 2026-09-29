/**
 * The label layers: Esri's reference overlay plus VARUNA's own street names, facilities and
 * chronic junctions (SPEC.md 6.7, ADR-0034).
 *
 * **Labels are computed from what is on screen, at the zoom that is on screen.** That is why
 * `CityMap`'s camera being controlled matters beyond framing: its view state is a React value, so
 * the label set is an ordinary derivation of it. An uncontrolled camera would have needed deck to
 * report its zoom back through an event before any of this could be decided.
 */

import { WebMercatorViewport } from "@deck.gl/core";
import { useMemo } from "react";

import { useTheme } from "@/lib/theme";

import type { Bbox } from "../basemap";
import {
  labelMarkerLayers,
  labelTextLayers,
  streetLabels,
  visibleLabels,
  type MapLabel,
} from "../labels";
import { labelLayers } from "../satellite";
import type { SegmentPath } from "./types";

export interface LabelLayerInput {
  baseSegments: readonly SegmentPath[];
  segments: readonly SegmentPath[];
  labels: readonly MapLabel[];
  showLabels: boolean;
  showSatellite: boolean;
  view: { longitude: number; latitude: number; zoom: number };
  size: { width: number; height: number } | null;
}

export function useLabelLayers({
  baseSegments,
  segments,
  labels,
  showLabels,
  showSatellite,
  view,
  size,
}: LabelLayerInput): unknown[] {
  const named = useMemo(
    () => [...streetLabels(baseSegments), ...streetLabels(segments), ...labels],
    [baseSegments, segments, labels],
  );

  // **Quantised, not exact.** The view is a new object on every frame of a pan or a fly-to, and a
  // label set derived from it recomputes sixty times a second - which rebuilds the layer array
  // sixty times a second, and the tile layers underneath spend their time being reconciled instead
  // of drawing. Rounding the camera to a tenth of a zoom level and ~100 m of position gives the
  // same labels and recomputes only when the view has meaningfully moved.
  const cameraKey = useMemo(
    () =>
      [
        Math.round(view.zoom * 10),
        Math.round(view.longitude * 1000),
        Math.round(view.latitude * 1000),
      ].join(":"),
    [view.zoom, view.longitude, view.latitude],
  );

  const drawnLabels = useMemo(() => {
    if (!showLabels || named.length === 0) return [];
    const zoom = view.zoom;
    // The viewport in lon/lat, so only labels the operator can actually see count against the cap.
    let visibleBounds: Bbox | null = null;
    if (size) {
      try {
        const viewport = new WebMercatorViewport({
          width: size.width,
          height: size.height,
          longitude: view.longitude,
          latitude: view.latitude,
          zoom,
        });
        const [[west, south], [east, north]] = viewport.getBounds() as unknown as [
          [number, number],
          [number, number],
        ];
        visibleBounds = [
          [west, south],
          [east, north],
        ];
      } catch {
        visibleBounds = null;
      }
    }
    return visibleLabels({ labels: named, zoom, bounds: visibleBounds });
    // `cameraKey` is the identity that matters; `view` is read for its exact values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showLabels, named, cameraKey, size]);

  // The theme is a dependency because every colour here is one: a switch swaps Esri's tile set
  // for the day or night one and re-tints the text and its halo (`labels.tsx`), in the same frame
  // the page's own tokens flip (M37).
  const { theme } = useTheme();
  return useMemo(
    () => [
      ...labelLayers({ enabled: showSatellite && showLabels, theme }),
      ...labelMarkerLayers(drawnLabels),
      ...labelTextLayers(drawnLabels),
    ],
    [showSatellite, showLabels, drawnLabels, theme],
  );
}
