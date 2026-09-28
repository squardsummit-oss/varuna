/**
 * Routes: the naive shortest path, the VARUNA route with its casing, alternates and the avoided
 * streets (SPEC.md 6.7, 7.4, motion M14).
 */

import { PathLayer } from "@deck.gl/layers";
import { useEffect, useMemo, useState } from "react";

import { ROUTE_CASING, ROUTE_COLOUR } from "./palette";
import type { RouteLine, RouteProgress } from "./types";

/**
 * The first `fraction` of a path, by cumulative length, with the cut edge interpolated.
 *
 * Interpolated rather than truncated to the nearest vertex: a route's legs are hundreds of metres
 * long, so snapping to vertices makes the draw-on jump in visible chunks instead of running
 * smoothly along the road.
 */
export function partialPath(path: [number, number][], fraction: number): [number, number][] {
  if (fraction >= 1 || path.length < 2) return path;
  if (fraction <= 0) return path.slice(0, 1);

  const lengths: number[] = [];
  let total = 0;
  for (let i = 1; i < path.length; i += 1) {
    const dx = path[i][0] - path[i - 1][0];
    const dy = path[i][1] - path[i - 1][1];
    const d = Math.hypot(dx, dy);
    lengths.push(d);
    total += d;
  }

  const target = total * fraction;
  const out: [number, number][] = [path[0]];
  let walked = 0;
  for (let i = 0; i < lengths.length; i += 1) {
    if (walked + lengths[i] >= target) {
      const t = lengths[i] > 0 ? (target - walked) / lengths[i] : 0;
      out.push([
        path[i][0] + (path[i + 1][0] - path[i][0]) * t,
        path[i][1] + (path[i + 1][1] - path[i][1]) * t,
      ]);
      break;
    }
    walked += lengths[i];
    out.push(path[i + 1]);
  }
  return out;
}

/** Motion M14's duration: the VARUNA route draws itself over 1.2 s (SPEC.md 8). */
export const ROUTE_DRAW_MS = 1200;

/**
 * 0 to 1 over {@link ROUTE_DRAW_MS} whenever the drawn route changes; 1 at once under reduced
 * motion, where SPEC.md 8 asks for both routes shown together rather than drawn.
 */
function useRouteDraw(key: string, reducedMotion: boolean): number {
  const [progress, setProgress] = useState(1);

  useEffect(() => {
    if (!key || reducedMotion) {
      // Reduced motion wants the finished route immediately. Setting it on the next frame rather
      // than synchronously keeps this out of the cascading-render path the lint rule guards, and a
      // frame is imperceptible for something whose whole point is that it does not animate.
      const settle = requestAnimationFrame(() => setProgress(1));
      return () => cancelAnimationFrame(settle);
    }
    let frame = 0;
    const started = performance.now();
    const tick = () => {
      const elapsed = performance.now() - started;
      const t = Math.min(elapsed / ROUTE_DRAW_MS, 1);
      // The same ease as every other motion in the catalogue (SPEC.md 8): fast out of the
      // origin, settling into the destination.
      setProgress(1 - (1 - t) ** 3);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [key, reducedMotion]);

  return progress;
}

/** What identifies a drawn route: re-planning the same trip after a profile change animates
 * again, and a scrub does not. */
export function routeKey(routes: readonly RouteLine[]): string {
  return routes.map((r) => `${r.kind}:${r.path.length}:${r.path[0]?.join(",") ?? ""}`).join("|");
}

/**
 * How far each route kind has drawn (motion M14). A new route draws itself in; the returned object
 * keeps its identity between frames that do not move it, so `CityMap`'s route memo does not
 * rebuild for nothing. MO8 replaces the timing here without touching `CityMap`.
 */
export function useRouteProgress(
  routes: readonly RouteLine[],
  reducedMotion: boolean,
): RouteProgress {
  const key = useMemo(() => routeKey(routes), [routes]);
  const varuna = useRouteDraw(key, reducedMotion);
  return useMemo(() => ({ naive: 1, varuna }), [varuna]);
}

export interface RouteLayerOptions {
  routes: readonly RouteLine[];
  /** From `useRouteProgress`. Only `varuna` is read today; `naive` and `avoidedAlpha` are MO8's. */
  progress: RouteProgress;
}

export function routeLayers({ routes, progress }: RouteLayerOptions): unknown[] {
  if (routes.length === 0) return [];
  // The casing is a wider, darker path drawn first: without it the route disappears wherever it
  // crosses a street of a similar tone, which on this map is most of them.
  return [
    new PathLayer<RouteLine>({
      id: "route-casing",
      data: routes.filter((r) => r.kind === "varuna" || r.kind === "alternate") as RouteLine[],
      getPath: (d) => d.path,
      getColor: ROUTE_CASING,
      getWidth: 7,
      widthUnits: "pixels",
      capRounded: true,
      jointRounded: true,
      pickable: false,
    }),
    new PathLayer<RouteLine>({
      id: "routes",
      data: routes as RouteLine[],
      // Motion M14: the VARUNA route draws itself over 1.2 s. `progress.varuna` runs 0 to 1 and
      // the path is truncated to that fraction of its length, so the line grows from the
      // origin rather than fading in - which is what makes it read as *a route being found*
      // instead of a shape appearing.
      getPath: (d) => (d.kind === "varuna" ? partialPath(d.path, progress.varuna) : d.path),
      getColor: (d) => ROUTE_COLOUR[d.kind],
      getWidth: (d) => (d.kind === "varuna" ? 5 : d.kind === "avoided" ? 4 : 3),
      widthUnits: "pixels",
      capRounded: true,
      jointRounded: true,
      pickable: false,
      updateTriggers: {
        getPath: [routes.length, progress.varuna],
        getColor: routes.length,
        getWidth: routes.length,
      },
    }),
  ];
}
