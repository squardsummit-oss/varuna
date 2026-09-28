import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { cityBounds } from "@/components/map/basemap";

import { FIT_PADDING_PX, toLatLngBounds, useGoogleFit, type FittableMap } from "./fit";

/**
 * jsdom's ResizeObserver (the setup file's stub) never calls back, so a real resize cannot be
 * observed through it. This one fires on demand, which is what makes the "re-fit until the reader
 * takes the camera" rule testable rather than merely asserted in a comment.
 */
const resizeCallbacks: (() => void)[] = [];
let originalResizeObserver: typeof ResizeObserver;

beforeEach(() => {
  resizeCallbacks.length = 0;
  originalResizeObserver = window.ResizeObserver;
  class FiringResizeObserver {
    constructor(private readonly callback: () => void) {}
    observe() {
      resizeCallbacks.push(this.callback);
    }
    unobserve() {}
    disconnect() {}
  }
  // Assignment, not `defineProperty`: the suite's setup file installs a non-configurable (but
  // writable) stub, so redefining the property throws.
  window.ResizeObserver = FiringResizeObserver as unknown as typeof ResizeObserver;
});

afterEach(() => {
  window.ResizeObserver = originalResizeObserver as typeof ResizeObserver;
});

function resize() {
  for (const callback of resizeCallbacks) callback();
}

function fakeMap() {
  const div = document.createElement("div");
  document.body.appendChild(div);
  const fits: { north: number; south: number; east: number; west: number }[] = [];
  const paddings: (number | undefined)[] = [];
  const listeners = new Map<string, () => void>();
  let removed = 0;

  const map: FittableMap = {
    fitBounds: (bounds, padding) => {
      fits.push(bounds);
      paddings.push(padding);
    },
    addListener: (event, handler) => {
      listeners.set(event, handler);
      return {
        remove: () => {
          removed += 1;
          listeners.delete(event);
        },
      };
    },
    getDiv: () => div,
  };
  return { map, div, fits, paddings, listeners, removedCount: () => removed };
}

function Host({ map }: { map: FittableMap | null }) {
  useGoogleFit(map, cityBounds("mumbai"));
  return null;
}

describe("toLatLngBounds", () => {
  it("turns [[west, south], [east, north]] into Google's literal", () => {
    expect(toLatLngBounds(cityBounds("mumbai"))).toEqual({
      west: 72.815,
      south: 18.995,
      east: 72.905,
      north: 19.135,
    });
  });
});

describe("useGoogleFit", () => {
  it("frames the city on first paint, with the padding and never a stored zoom", () => {
    const { map, fits, paddings } = fakeMap();
    render(<Host map={map} />);
    expect(fits).toEqual([toLatLngBounds(cityBounds("mumbai"))]);
    expect(paddings).toEqual([FIT_PADDING_PX]);
  });

  it("re-frames when its box changes, so the map fits at every viewport", () => {
    const { map, fits } = fakeMap();
    render(<Host map={map} />);
    expect(fits).toHaveLength(1);
    resize();
    resize();
    expect(fits).toHaveLength(3);
  });

  it("stops re-framing once the reader drags the map", () => {
    const { map, fits, listeners } = fakeMap();
    render(<Host map={map} />);
    listeners.get("dragstart")?.();
    resize();
    expect(fits).toHaveLength(1);
  });

  it("treats a wheel gesture as the reader taking the camera", () => {
    const { map, fits, div } = fakeMap();
    render(<Host map={map} />);
    div.dispatchEvent(new Event("wheel"));
    resize();
    expect(fits).toHaveLength(1);
  });

  it("removes its listeners on unmount", () => {
    const { map, removedCount } = fakeMap();
    const view = render(<Host map={map} />);
    view.unmount();
    expect(removedCount()).toBe(1);
  });

  it("does nothing at all while there is no map", () => {
    expect(() => render(<Host map={null} />)).not.toThrow();
  });
});
