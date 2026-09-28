"use client";

import { DUR_MS } from "@/lib/motion";

import { useTween } from "./use-tween";

export interface LayerFadeOptions {
  /** Fade length; motion M19 states 400 ms (SPEC.md 8). */
  durationMs?: number;
  /** Overrides the OS reduced-motion preference, as in `useTween`. */
  reduced?: boolean;
}

/**
 * Opacity for one map layer under motion M19: 0 while the layer is not ready, then a 400 ms fade
 * to 1 on the catalogue easing when it becomes ready. Under reduced motion a ready layer is at 1
 * at once, which is M19's "instant" fallback.
 *
 * A layer that is already ready when the component mounts reads 1 straight away, so reopening a
 * finished onboarding does not replay the stack. A layer that goes away and comes back (a new
 * job) fades in again.
 *
 * One call per layer: `useLayerFade("roads", layersReady.includes("roads"))`. The value is meant
 * for a deck.gl layer's `opacity` prop; keep that layer's data and accessors memoised so a frame
 * of the fade changes nothing but the opacity.
 */
export function useLayerFade(
  layerId: string,
  ready: boolean,
  options: LayerFadeOptions = {},
): number {
  const { durationMs = DUR_MS.layerFade, reduced } = options;
  const progress = useTween(ready ? layerId : null, durationMs, { reduced });
  return ready ? progress : 0;
}
