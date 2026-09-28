/**
 * MO1's proof that splitting `city-map.tsx` changed nothing deck draws.
 *
 * `@deck.gl/react` is replaced by a recorder, so every render's `layers` array and camera land here
 * as data. Each scenario renders `CityMap` the way a screen does and compares the serialized layers
 * (ids, order, literal props, accessors evaluated on the data) with the fixture captured from the
 * monolith before any code moved. `CAPTURE=1` rewrites the fixture; it was run once on commit
 * "capture the monolith", and a second time for chunk INTEGRATE's defect 3, which caps the
 * ground-truth pin and its ripple in pixels so a 70 m marker cannot become a 248 px disc over the
 * junction it marks. That recapture added 28 `radiusMaxPixels` and 9 `radiusMinPixels` lines and
 * removed nothing, which is the whole of that change and the check that nothing else drifted in
 * with it. A recapture is never how a failure is made to pass: the diff is read line by line
 * first, and a line that is not the change being made is a regression.
 */

import { act, render } from "@testing-library/react";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { serializeLayers } from "./serialize";
import * as fx from "./fixture";

interface Recorded {
  layers: unknown[];
  viewState: unknown;
  controller: unknown;
  pickingRadius: unknown;
  getTooltip?: (info: { object?: unknown }) => unknown;
  onClick?: unknown;
  onViewStateChange?: unknown;
  animate: unknown;
}

const renders: Recorded[] = [];

vi.mock("@deck.gl/react", () => ({
  default: (props: Record<string, unknown>) => {
    renders.push({
      layers: props.layers as unknown[],
      viewState: props.viewState,
      controller: props.controller,
      pickingRadius: props.pickingRadius,
      getTooltip: props.getTooltip as Recorded["getTooltip"],
      onClick: props.onClick,
      onViewStateChange: props.onViewStateChange,
      animate: props._animate ?? false,
    });
    return null;
  },
}));

// Imported after the mock so `CityMap` binds to the recorder.
const { CityMap } = await import("../../city-map");

const FIXTURE = path.resolve(__dirname, "../__fixtures__/monolith-layers.json");
const CAPTURE = process.env.CAPTURE === "1";
const captured: Record<string, unknown> = {};

/** A manually flushed animation frame queue with a controlled clock. */
let frameQueue: FrameRequestCallback[] = [];
let now = 1000;

function flushFrames(atMs: number) {
  now = atMs;
  const queue = frameQueue;
  frameQueue = [];
  act(() => {
    for (const callback of queue) callback(atMs);
  });
}

function setReducedMotion(reduced: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: reduced && query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

beforeEach(() => {
  renders.length = 0;
  frameQueue = [];
  now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frameQueue.push(callback);
    return frameQueue.length;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  setReducedMotion(false);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function last(): Recorded {
  const recorded = renders.at(-1);
  if (!recorded) throw new Error("CityMap never rendered DeckGL");
  return recorded;
}

/** Everything about one render that deck or the viewer could see. */
function picture(recorded: Recorded, container: HTMLElement) {
  const firstWet = fx.segments[0];
  return {
    layers: serializeLayers(recorded.layers),
    viewState: JSON.parse(
      JSON.stringify(recorded.viewState, (_k, v: unknown) =>
        v && typeof v === "object" && !Array.isArray(v) && v.constructor?.name !== "Object"
          ? `[${v.constructor.name}]`
          : v,
      ),
    ) as unknown,
    controller: recorded.controller ?? null,
    pickingRadius: recorded.pickingRadius ?? null,
    animate: recorded.animate,
    tooltip: recorded.getTooltip
      ? [recorded.getTooltip({ object: firstWet }), recorded.getTooltip({})]
      : null,
    onClick: typeof recorded.onClick === "function",
    onViewStateChange: typeof recorded.onViewStateChange === "function",
    // The scrim and the credit line, i.e. the DOM around the canvas.
    html: container.innerHTML,
  };
}

function check(name: string, value: unknown) {
  if (CAPTURE) {
    captured[name] = value;
    return;
  }
  const expected = (JSON.parse(readFileSync(FIXTURE, "utf8")) as Record<string, unknown>)[name];
  expect(expected, `fixture has no scenario "${name}"`).toBeDefined();
  expect(JSON.parse(JSON.stringify(value))).toEqual(expected);
}

describe("CityMap renders the layers the monolith rendered", () => {
  afterEach(() => {
    if (CAPTURE) {
      const previous = existsSync(FIXTURE)
        ? (JSON.parse(readFileSync(FIXTURE, "utf8")) as Record<string, unknown>)
        : {};
      writeFileSync(FIXTURE, `${JSON.stringify({ ...previous, ...captured }, null, 1)}\n`);
    }
  });

  it("console, everything on", () => {
    const { container } = render(<CityMap {...fx.consoleProps()} />);
    check("console", picture(last(), container));
  });

  it("probability mode at 30 cm", () => {
    const { container } = render(
      <CityMap {...fx.consoleProps({ step: 2, probabilityThresholdCm: 30 })} />,
    );
    check("probability", picture(last(), container));
  });

  it("public map passability, nothing pickable", () => {
    const { container } = render(
      <CityMap
        {...fx.consoleProps({
          mode: "public",
          passableBelowCm: 30,
          onSegmentPick: undefined,
          step: 2,
          showDrains: false,
        })}
      />,
    );
    check("public", picture(last(), container));
  });

  it("what-if diff half wiped", () => {
    const { container } = render(
      <CityMap {...fx.consoleProps({ diffMode: true, diffProgress: 0.5, routes: [] })} />,
    );
    check("diff-half", picture(last(), container));
  });

  it("what-if diff fully drawn", () => {
    const { container } = render(
      <CityMap {...fx.consoleProps({ diffMode: true, diffProgress: 1, routes: [] })} />,
    );
    check("diff-full", picture(last(), container));
  });

  it("hero, quiet", () => {
    const { container } = render(
      <CityMap
        {...fx.consoleProps({
          mode: "hero",
          showSatellite: false,
          showRaster: false,
          showLabels: false,
          showBuildings: false,
          attribution: false,
          onSegmentPick: undefined,
          routes: [],
          isochrones: [],
          truthPins: [],
          step: 3,
        })}
      />,
    );
    check("hero", picture(last(), container));
  });

  it("empty map", () => {
    const { container } = render(
      <CityMap
        frames={[]}
        rasterBounds={null}
        baseSegments={[]}
        segments={[]}
        surcharge={[]}
        hotspots={[]}
        step={0}
      />,
    );
    check("empty", picture(last(), container));
  });

  it("route draw-on and surcharge pulse mid-flight", () => {
    const { container } = render(<CityMap {...fx.consoleProps()} />);
    // The route draw and the pulse loop each queue a frame on mount; run them 600 ms in.
    flushFrames(1600);
    check("motion-600ms", picture(last(), container));
    flushFrames(2000);
    check("motion-1000ms", picture(last(), container));
    flushFrames(3000);
    check("motion-2000ms", picture(last(), container));
  });

  it("reduced motion", () => {
    setReducedMotion(true);
    const { container } = render(<CityMap {...fx.consoleProps()} />);
    flushFrames(1600);
    check("reduced", picture(last(), container));
  });

  it("flies to a focus", () => {
    const { container } = render(
      <CityMap
        {...fx.consoleProps({ focus: { lon: 72.841, lat: 19.012, key: "k1", zoom: 15 } })}
      />,
    );
    check("focus", picture(last(), container));
  });

  it("a scrub rebuilds only the run's layers", () => {
    const props = fx.consoleProps();
    const { rerender } = render(<CityMap {...props} />);
    const before = last().layers as { id: string }[];
    rerender(<CityMap {...props} step={2} />);
    const after = last().layers as { id: string }[];
    const rebuilt = after.filter((layer) => !before.includes(layer)).map((layer) => layer.id);
    check("scrub-rebuilt", rebuilt);
  });

  it("a toggled selection rebuilds only the run's layers", () => {
    const props = fx.consoleProps();
    const { rerender } = render(<CityMap {...props} />);
    const before = last().layers as { id: string }[];
    rerender(<CityMap {...props} selectedHotspotId="H-sion" />);
    const after = last().layers as { id: string }[];
    check(
      "select-rebuilt",
      after.filter((layer) => !before.includes(layer)).map((layer) => layer.id),
    );
  });
});
