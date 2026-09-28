/**
 * Motion M15 (SPEC.md 8): isochrone polygons morph over 300 ms on scrub, and swap instantly
 * under reduced motion. The morph runs on deck's GPU transitions, so what is testable here is the
 * contract deck is handed: the transition spec with motion on, none with it off.
 */

import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DUR_MS, easeUi } from "@/lib/motion";

import {
  ISOCHRONE_TRANSITIONS,
  isochroneLayers,
  isochronesMorph,
  useDisplayedIsochrones,
} from "../isochrones";
import type { Isochrone } from "../types";

interface PolygonProps {
  data: Isochrone[];
  transitions?: typeof ISOCHRONE_TRANSITIONS;
  getPolygon: (d: Isochrone) => [number, number][];
}

function props(layers: unknown[]): PolygonProps {
  expect(layers).toHaveLength(1);
  return (layers[0] as { props: PolygonProps }).props;
}

const square = (lon: number, lat: number, r: number): [number, number][] => [
  [lon - r, lat - r],
  [lon + r, lat - r],
  [lon + r, lat + r],
  [lon - r, lat + r],
  [lon - r, lat - r],
];

// Two slices of KEM Hospital's catchment: dry-ish at 08:40, shrunk later. The later 15-minute
// hull has a different vertex count, which is the case deck's padded transition exists for.
const at0840: Isochrone[] = [
  { minutes: 5, rings: [square(72.842, 19.002, 0.004)] },
  { minutes: 10, rings: [square(72.842, 19.002, 0.009)] },
  { minutes: 15, rings: [square(72.842, 19.002, 0.014)] },
];
const at1040: Isochrone[] = [
  {
    minutes: 15,
    rings: [
      square(72.842, 19.002, 0.007)
        .slice(0, 4)
        .concat([[72.835, 18.995]]),
    ],
  },
  { minutes: 5, rings: [square(72.842, 19.002, 0.003)] },
  { minutes: 10, rings: [square(72.842, 19.002, 0.005)] },
];

describe("M15 isochrone morph", () => {
  it("hands deck a 300 ms transition on the polygon and its fill, on the catalogue easing", () => {
    expect(ISOCHRONE_TRANSITIONS.getPolygon.duration).toBe(DUR_MS.isochroneMorph);
    expect(DUR_MS.isochroneMorph).toBe(300);
    expect(ISOCHRONE_TRANSITIONS.getPolygon.easing).toBe(easeUi);
    expect(ISOCHRONE_TRANSITIONS.getFillColor.duration).toBe(300);

    const { result } = renderHook(() => useDisplayedIsochrones(at0840, false));
    expect(result.current).toBe(at0840);
    expect(props(isochroneLayers({ isochrones: result.current })).transitions).toBe(
      ISOCHRONE_TRANSITIONS,
    );
  });

  it("swaps instantly under reduced motion, drawing exactly the same polygons", () => {
    const { result } = renderHook(() => useDisplayedIsochrones(at0840, true));
    expect(isochronesMorph(result.current)).toBe(false);
    const reduced = props(isochroneLayers({ isochrones: result.current }));
    const moving = props(isochroneLayers({ isochrones: at0840 }));
    expect(reduced.transitions).toBeFalsy();
    expect(reduced.data.map(reduced.getPolygon)).toEqual(moving.data.map(moving.getPolygon));
  });

  it("a reduced-motion slice is remembered for the render it belongs to, not forever", () => {
    const { result, rerender } = renderHook(
      ({ slice, reduced }: { slice: Isochrone[]; reduced: boolean }) =>
        useDisplayedIsochrones(slice, reduced),
      { initialProps: { slice: at0840, reduced: true } },
    );
    const first = result.current;
    rerender({ slice: at0840, reduced: true });
    expect(result.current).toBe(first);
    rerender({ slice: at0840, reduced: false });
    expect(isochronesMorph(result.current)).toBe(true);
    expect(isochronesMorph(at0840)).toBe(true);
  });

  it("an explicit option overrides what the hook decided", () => {
    expect(
      props(isochroneLayers({ isochrones: at0840, reducedMotion: true })).transitions,
    ).toBeFalsy();
    const { result } = renderHook(() => useDisplayedIsochrones(at0840, true));
    expect(
      props(isochroneLayers({ isochrones: result.current, reducedMotion: false })).transitions,
    ).toBe(ISOCHRONE_TRANSITIONS);
  });

  it("orders by band, so each band tweens into the same band of the next slice", () => {
    const a = props(isochroneLayers({ isochrones: at0840 }));
    const b = props(isochroneLayers({ isochrones: at1040 }));
    expect(a.data.map((d) => d.minutes)).toEqual([15, 10, 5]);
    expect(b.data.map((d) => d.minutes)).toEqual([15, 10, 5]);
  });

  it("draws nothing for an empty slice, with or without motion", () => {
    expect(isochroneLayers({ isochrones: [] })).toEqual([]);
    expect(isochroneLayers({ isochrones: [], reducedMotion: true })).toEqual([]);
  });
});
