/**
 * The seams MO1 pre-wired for later chunks are inert: setting every one of them changes nothing
 * deck draws. Each seam names the chunk that makes it do something (MO3, MO5, MO8, MO9, MO10, PU8);
 * when that chunk lands it updates the fixture scenario it changes, and this test with it.
 */

import { act, render, renderHook } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { drainsLayers } from "../drains";
import { hotspotRingsLayers } from "../hotspots";
import { inletLayers } from "../inlets";
import { isochroneLayers, useDisplayedIsochrones } from "../isochrones";
import { wetStreetsLayers } from "../streets";
import { surchargeLayers } from "../surcharge";
import type { InletPoint, ReversedEdgePath } from "../types";
import * as fx from "./fixture";
import { serializeLayers } from "./serialize";

const renders: { layers: unknown[]; animate: unknown }[] = [];

vi.mock("@deck.gl/react", () => ({
  default: (props: Record<string, unknown>) => {
    renders.push({ layers: props.layers as unknown[], animate: props._animate ?? false });
    return null;
  },
}));

const { CityMap } = await import("../../city-map");

const FIXTURE = path.resolve(__dirname, "../__fixtures__/monolith-layers.json");

const reversedEdges: ReversedEdgePath[] = [
  {
    edgeId: "E-outfall",
    path: [
      [72.838, 19.019],
      [72.84, 19.02],
    ],
    minQ: -0.3,
    tidal: true,
  },
];

const inlets: InletPoint[] = [{ lon: 72.841, lat: 19.012, kappa: 0.4, learned: true }];

beforeEach(() => {
  renders.length = 0;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
});

describe("MO1 seams draw nothing yet", () => {
  it("CityMap with every seam set renders the console layers, plus MO3's reversed-flow dash", () => {
    render(
      <CityMap
        {...fx.consoleProps({
          playing: true,
          reversedEdges,
          drainCrossFadeMs: 300,
          onDrainHover: () => {},
          inlets,
          surchargeStyle: "ring",
        })}
      />,
    );
    const recorded = renders.at(-1);
    if (!recorded) throw new Error("CityMap never rendered DeckGL");
    const expected = (
      JSON.parse(readFileSync(FIXTURE, "utf8")) as Record<string, { layers: { id: string }[] }>
    ).console.layers;
    const drawn = JSON.parse(JSON.stringify(serializeLayers(recorded.layers))) as {
      id: string;
      [key: string]: unknown;
    }[];
    // MO3 made `reversedEdges` draw: the tidal edge is kept at every zoom, in the section 6.7 slot
    // after the hotspot rings and under the surcharge markers. Every other seam is still inert.
    const ids = drawn.map((layer) => layer.id);
    const at = ids.indexOf("reversed-flow");
    expect(at).toBeGreaterThan(ids.indexOf("hotspot-rings"));
    expect(at).toBeLessThan(ids.indexOf("surcharge"));
    expect(drawn[at]).toMatchObject({ class: "PathLayer", flowAnimated: true });
    expect(drawn.filter((layer) => layer.id !== "reversed-flow")).toEqual(expected);
    // Markers and a dash on screen, motion allowed: deck runs its own redraw loop.
    expect(recorded.animate).toBe(true);
  });

  it("the pulse and the dash cost CityMap no renders: nothing queues a frame for them", () => {
    // M8 and M9 run on deck's clock. With nothing else animating (no route to draw, no pin to
    // drop), mounting the console map must leave no React animation loop behind.
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {});
    try {
      render(
        <CityMap
          {...fx.consoleProps({ routes: [], truthPins: [], isochrones: [], reversedEdges })}
        />,
      );
      expect(renders.at(-1)?.animate).toBe(true);
      // The route hook settles its progress in one frame even with no route; run whatever is
      // queued until nothing re-queues. A React pulse or dash loop would queue a frame every frame.
      let rounds = 0;
      while (frames.length > 0 && rounds < 10) {
        const queue = frames.splice(0);
        act(() => {
          for (const callback of queue) callback(performance.now() + 16 * (rounds + 1));
        });
        rounds += 1;
      }
      expect(frames).toHaveLength(0);
      expect(rounds).toBeLessThanOrEqual(1);
      const settled = renders.length;
      act(() => {
        for (const callback of frames.splice(0)) callback(performance.now() + 2000);
      });
      expect(renders.length).toBe(settled);
      expect(renders.at(-1)?.animate).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("a dropping pin costs CityMap no renders either: deck draws the drop (M18)", () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {});
    try {
      render(
        <CityMap
          {...fx.consoleProps({
            routes: [],
            isochrones: [],
            reversedEdges: [],
            surcharge: [],
            truthPins: [
              { id: "P-1", lon: 72.858, lat: 19.032, name: "Gandhi Market", dropStartMs: 1 },
            ],
          })}
        />,
      );
      const ids = (renders.at(-1)?.layers as { id: string }[]).map((layer) => layer.id);
      expect(ids).toContain("truth-drop-P-1");
      expect(ids).toContain("truth-ripple-P-1");
      let rounds = 0;
      while (frames.length > 0 && rounds < 10) {
        const queue = frames.splice(0);
        act(() => {
          for (const callback of queue) callback(performance.now() + 16 * (rounds + 1));
        });
        rounds += 1;
      }
      // The drop is the layer's own: CityMap queues no frame for it and does not render again.
      // (The hook that stamps the pin is checked for the same thing in use-truth-pins.test.ts.)
      expect(frames).toHaveLength(0);
      expect(rounds).toBeLessThanOrEqual(1);
      const settled = renders.length;
      act(() => {
        for (const callback of frames.splice(0)) callback(performance.now() + 300);
      });
      expect(renders.length).toBe(settled);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("each builder ignores its seam argument", () => {
    const same = (a: unknown[], b: unknown[]) =>
      expect(serializeLayers(a)).toEqual(serializeLayers(b));

    // The plain drains every screen but `/drains` draws still ignore the cross-fade (M12) and the
    // hover: `/drains`' learned overlay (`withLearned`) is what honours them, pinned in
    // `drains-learned.test.ts`.
    same(
      drainsLayers({ drains: fx.drains, show: true, crossFadeMs: 300, onHover: () => {} }),
      drainsLayers({ drains: fx.drains, show: true }),
    );
    const streets = {
      segments: fx.segments,
      step: 1,
      show: true,
      diffMode: false,
      wipeLon: Number.POSITIVE_INFINITY,
      pickable: true,
    };
    same(
      wetStreetsLayers({ ...streets, playing: true, reducedMotion: true }),
      wetStreetsLayers(streets),
    );
    const rings = { hotspots: fx.hotspots, selectedHotspotId: "H-sion", show: true };
    same(hotspotRingsLayers({ ...rings, reducedMotion: true }), hotspotRingsLayers(rings));
    same(
      surchargeLayers({ surcharge: fx.surcharge, show: true, reducedMotion: false, style: "ring" }),
      surchargeLayers({ surcharge: fx.surcharge, show: true, reducedMotion: false }),
    );
    expect(inletLayers({ inlets, show: true })).toEqual([]);
  });

  it("the isochrone tween hands back the slice it was given", () => {
    const { result } = renderHook(() => useDisplayedIsochrones(fx.isochrones, false));
    expect(result.current).toBe(fx.isochrones);
    expect(serializeLayers(isochroneLayers({ isochrones: result.current }))).toEqual(
      serializeLayers(isochroneLayers({ isochrones: fx.isochrones })),
    );
  });
});
