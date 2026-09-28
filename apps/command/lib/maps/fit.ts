"use client";

/**
 * The citizen map's camera: fit the city, then get out of the reader's way (TECH_SPEC 2.4).
 *
 * The rule is the console's (`components/map/layers/camera.ts`) in Google's vocabulary: **the fit
 * is derived, never stored**. A stored zoom looks right on the laptop it was chosen on and wrong
 * on a phone, a 4K wall and a resized window; `fitBounds` against the AOI is right on all of them.
 * So the map re-fits whenever its box changes - until the reader pans or zooms, after which the
 * camera is theirs and nothing takes it back.
 *
 * The globe entry (UI_SPEC 2) ends on these same bounds, which is what lets the cross-fade land on
 * an already framed map instead of a world view snapping into place.
 */

import { useEffect, useRef } from "react";

import type { Bbox } from "@/components/map/basemap";

/** Google's literal bounds object. */
export interface LatLngBoundsLiteral {
  north: number;
  south: number;
  east: number;
  west: number;
}

/**
 * The slice of `google.maps.Map` this module uses.
 *
 * Structural, for the reason given in `overlay.ts`: the ambient `google` namespace is not in this
 * app's type program, and a structural type both compiles and lets a test drive the hook.
 */
export interface FittableMap {
  fitBounds(bounds: LatLngBoundsLiteral, padding?: number): void;
  addListener(event: string, handler: () => void): { remove: () => void };
  getDiv(): HTMLElement | null;
}

/** `[[west, south], [east, north]]` as Google writes it. */
export function toLatLngBounds(bounds: Bbox): LatLngBoundsLiteral {
  const [[west, south], [east, north]] = bounds;
  return { north, south, east, west };
}

/**
 * Framing margin in pixels. Small, for the same reason as the console's: the legend and the sheet
 * float over the map, so the city already has furniture around it.
 */
export const FIT_PADDING_PX = 16;

/** Gestures that mean the reader has taken the camera. `dragstart` is Google's own. */
const OWNING_DOM_EVENTS = ["wheel", "dblclick"] as const;

/**
 * Frame `bounds` on first paint and on every resize, until the reader moves the camera.
 *
 * Returns nothing: ownership lives in a ref, because a re-render on the reader's first scroll
 * would be a re-render that changes nothing on screen.
 */
export function useGoogleFit(map: FittableMap | null, bounds: Bbox): void {
  const owned = useRef(false);
  const [[west, south], [east, north]] = bounds;

  useEffect(() => {
    if (!map) return;
    const literal = { north, south, east, west };
    const fit = () => {
      if (owned.current) return;
      map.fitBounds(literal, FIT_PADDING_PX);
    };
    fit();

    const take = () => {
      owned.current = true;
    };
    const drag = map.addListener("dragstart", take);
    const div = map.getDiv();
    for (const event of OWNING_DOM_EVENTS) {
      div?.addEventListener(event, take, { passive: true });
    }

    // The box, not the window: the map pane shrinks when the rail appears at 1024 px without the
    // window changing size at all.
    let observer: ResizeObserver | null = null;
    if (div && typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(fit);
      observer.observe(div);
    }

    return () => {
      drag.remove();
      for (const event of OWNING_DOM_EVENTS) {
        div?.removeEventListener(event, take);
      }
      observer?.disconnect();
    };
  }, [map, north, south, east, west]);
}
