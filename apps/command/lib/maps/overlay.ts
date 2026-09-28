"use client";

/**
 * The deck.gl overlay that draws VARUNA's water over Google's basemap (TECH_SPEC 2.2).
 *
 * **Overlaid, not interleaved.** `interleaved: true` renders deck's layers inside Google's own
 * WebGL context, which has no multisampling; every path then aliases, and a 1.6 px street is
 * exactly the thing that cannot afford it. Overlaid mode gives deck its own canvas over the tiles,
 * keeps the layer modules under `components/map/layers/` unchanged, and costs one compositing pass.
 *
 * The lifecycle is the whole point of this module: **one overlay per map**, its layers pushed by
 * `setProps`, and `finalize()` on unmount. Creating a second overlay leaks a WebGL context, and a
 * browser gives out about sixteen before it starts dropping the oldest - which on this screen
 * means the console's map going black after a few visits to the dashboard.
 */

import { GoogleMapsOverlay } from "@deck.gl/google-maps";
import { useEffect, useRef } from "react";

/**
 * The map instance the overlay attaches to.
 *
 * Structural rather than `google.maps.Map`: `@types/google.maps` is a transitive dependency whose
 * ambient namespace is not in this app's type program, so naming it would fail `pnpm typecheck`.
 * Nothing here calls a method on it - it is handed straight back to deck.
 */
export type GoogleMapHandle = object;

/** What `useGoogleDeckOverlay` builds, so a test can substitute a counting fake. */
export type OverlayFactory = (options: { interleaved: boolean }) => {
  setMap: (map: GoogleMapHandle | null) => void;
  setProps: (props: { layers: unknown[] }) => void;
  finalize: () => void;
};

const defaultFactory: OverlayFactory = (options) =>
  new GoogleMapsOverlay(options) as unknown as ReturnType<OverlayFactory>;

/**
 * Attach one deck.gl overlay to a Google map and keep its layers current.
 *
 * `map` is null until the Google bootstrap has created it, and null again while the fallback map
 * is on screen; both are ordinary states, not errors, and no overlay exists in either.
 */
export function useGoogleDeckOverlay(
  map: GoogleMapHandle | null,
  layers: readonly unknown[],
  factory: OverlayFactory = defaultFactory,
): void {
  const overlayRef = useRef<ReturnType<OverlayFactory> | null>(null);

  useEffect(() => {
    if (!map) return;
    const overlay = factory({ interleaved: false });
    overlayRef.current = overlay;
    overlay.setMap(map);
    return () => {
      overlayRef.current = null;
      // Detach before finalize: finalizing an attached overlay leaves Google holding a disposed
      // renderer, and the next tile redraw throws inside Google's own code, where nothing on this
      // screen can catch it.
      overlay.setMap(null);
      overlay.finalize();
    };
  }, [map, factory]);

  // Declared after the effect above, so on the mounting commit React runs that one first and this
  // one draws the first frame's layers before the browser paints. No ref-during-render needed.
  useEffect(() => {
    overlayRef.current?.setProps({ layers: [...layers] });
  }, [layers, map]);
}
