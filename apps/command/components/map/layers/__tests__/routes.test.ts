/**
 * `partialPath`, the geometry under motion M14's draw-on, and the route-progress seam MO8 fills.
 */

import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { partialPath, routeKey, useRouteProgress } from "../routes";
import type { RouteLine } from "../types";

const PATH: [number, number][] = [
  [72.841, 19.003],
  [72.835, 19.015],
  [72.85, 19.03],
  [72.862, 19.041],
];

function length(path: readonly [number, number][]): number {
  let total = 0;
  for (let i = 1; i < path.length; i += 1) {
    total += Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]);
  }
  return total;
}

describe("partialPath", () => {
  it("is the first vertex at 0 and the whole path at 1", () => {
    expect(partialPath(PATH, 0)).toEqual([PATH[0]]);
    expect(partialPath(PATH, -0.2)).toEqual([PATH[0]]);
    expect(partialPath(PATH, 1)).toBe(PATH);
    expect(partialPath(PATH, 1.5)).toBe(PATH);
  });

  it("returns a path with fewer than two vertices as it is", () => {
    const single: [number, number][] = [[72.84, 19.0]];
    expect(partialPath(single, 0.5)).toBe(single);
  });

  it("has a length of fraction x total, to 1e-9", () => {
    const total = length(PATH);
    for (const fraction of [0.05, 0.25, 0.5, 0.61, 0.9, 0.999]) {
      expect(Math.abs(length(partialPath(PATH, fraction)) - fraction * total)).toBeLessThan(1e-9);
    }
  });

  it("cuts on the segment it reached, not at a vertex", () => {
    const cut = partialPath(PATH, 0.5);
    const end = cut[cut.length - 1];
    const a = PATH[cut.length - 2];
    const b = PATH[cut.length - 1];
    // Collinear with the segment it lies on, and between its ends.
    const cross = (b[0] - a[0]) * (end[1] - a[1]) - (b[1] - a[1]) * (end[0] - a[0]);
    expect(Math.abs(cross)).toBeLessThan(1e-12);
    expect(end[0]).toBeGreaterThanOrEqual(Math.min(a[0], b[0]));
    expect(end[0]).toBeLessThanOrEqual(Math.max(a[0], b[0]));
    expect(PATH).not.toContainEqual(end);
  });
});

describe("useRouteProgress", () => {
  const routes: RouteLine[] = [
    { id: "naive", kind: "naive", path: PATH.slice(0, 2) },
    { id: "varuna", kind: "varuna", path: PATH },
  ];

  it("keys a route on its kind, vertex count and origin", () => {
    expect(routeKey(routes)).toBe("naive:2:72.841,19.003|varuna:4:72.841,19.003");
    expect(routeKey([])).toBe("");
  });

  it("draws the naive route whole: MO8 owns its timing, not yet built", () => {
    const { result } = renderHook(() => useRouteProgress(routes, true));
    expect(result.current.naive).toBe(1);
    expect(result.current.avoidedAlpha).toBeUndefined();
  });
});
