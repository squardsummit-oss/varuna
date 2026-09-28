/**
 * The camera's fit key and the view it keeps (`layers/camera.ts`), which is what lets the
 * console's full view put back the camera the operator had.
 *
 * jsdom has no layout, so the container's size is handed to the hook through a stubbed
 * `ResizeObserver`; the fit itself is deck's `WebMercatorViewport.fitBounds`, and only which view
 * wins is asserted here, never deck's arithmetic.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { usablePadding, useCityCamera, type CityCameraInput, type ViewState } from "../camera";
import type { Bbox } from "../../basemap";

const AFFECTED: Bbox = [
  [72.836, 19.027],
  [72.904, 19.092],
];
const FULL: Bbox = [
  [72.84, 19.0],
  [72.87, 19.03],
];

/** Every observer created reports this size to its callback when it starts observing. */
let observed: { width: number; height: number } = { width: 1200, height: 800 };

/** Reports `observed` to its callback as soon as it starts observing. */
class MeasuringObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe() {
    this.callback(
      [{ contentRect: observed } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }
  disconnect() {}
  unobserve() {}
}

// `vitest.setup.ts` defines a silent stub that is writable but not configurable, so it is
// swapped by assignment rather than `vi.stubGlobal`.
const silentObserver = window.ResizeObserver;
beforeEach(() => {
  window.ResizeObserver = MeasuringObserver as unknown as typeof ResizeObserver;
});

afterEach(() => {
  window.ResizeObserver = silentObserver;
  observed = { width: 1200, height: 800 };
});

function mount(initial: Partial<CityCameraInput>) {
  const container = document.createElement("div");
  return renderHook(
    (props: Partial<CityCameraInput>) => {
      const camera = useCityCamera({
        frame: AFFECTED,
        focus: null,
        reducedMotion: true,
        interactive: true,
        ...props,
      });
      // Attach the element before the effect that observes it runs.
      (camera.containerRef as { current: HTMLDivElement | null }).current = container;
      return camera;
    },
    { initialProps: initial },
  );
}

/** Where an operator drags the map to: well away from any fit of either box. */
const DRAGGED: ViewState = { longitude: 72.95, latitude: 19.2, zoom: 15.5, bearing: 0, pitch: 0 };

function drag(result: { current: ReturnType<typeof useCityCamera> }) {
  act(() => {
    result.current.onViewStateChange?.({
      viewState: DRAGGED,
      interactionState: { isDragging: true },
    });
  });
}

describe("the camera's fit key", () => {
  it("re-fits on a new key, dropping the operator's pan", () => {
    const { result, rerender } = mount({ fitKey: "affected" });
    const fitted = result.current.viewState;
    drag(result);
    expect(result.current.viewState.zoom).toBe(DRAGGED.zoom);

    rerender({ fitKey: "full-view", frame: FULL });
    expect(result.current.viewState.longitude).not.toBe(DRAGGED.longitude);
    expect(result.current.viewState.longitude).not.toBe(fitted.longitude);

    // Without `keepViewOf`, coming back re-fits too: the pan is gone for good.
    rerender({ fitKey: "affected", frame: AFFECTED });
    expect(result.current.viewState).toEqual(fitted);
  });

  it("gives back the view the operator had on the kept key", () => {
    const { result, rerender } = mount({ fitKey: "affected", keepViewOf: "affected" });
    drag(result);

    rerender({ fitKey: "full-view", frame: FULL, keepViewOf: "affected" });
    const full = result.current.viewState;
    expect(full.longitude).not.toBe(DRAGGED.longitude);

    rerender({ fitKey: "affected", frame: AFFECTED, keepViewOf: "affected" });
    expect(result.current.viewState).toMatchObject(DRAGGED);
    expect(result.current.viewState.transitionDuration).toBe(0);
  });

  it("returns to the fit when nobody had moved the camera, and the fit still follows the frame", () => {
    const { result, rerender } = mount({ fitKey: "affected", keepViewOf: "affected" });
    const fitted = result.current.viewState;

    rerender({ fitKey: "full-view", frame: FULL, keepViewOf: "affected" });
    rerender({ fitKey: "affected", frame: AFFECTED, keepViewOf: "affected" });
    expect(result.current.viewState).toEqual(fitted);

    // Still the fit's, so a new frame (a new run) re-frames it.
    rerender({ fitKey: "affected", frame: FULL, keepViewOf: "affected" });
    expect(result.current.viewState.longitude).not.toBe(fitted.longitude);
  });

  it("spends the kept view once, so a second trip re-fits the full view afresh", () => {
    const { result, rerender } = mount({ fitKey: "affected", keepViewOf: "affected" });
    drag(result);
    rerender({ fitKey: "full-view", frame: FULL, keepViewOf: "affected" });
    const firstFull = result.current.viewState;
    // A pan inside full view is not kept: full view always opens on the water.
    drag(result);
    rerender({ fitKey: "affected", frame: AFFECTED, keepViewOf: "affected" });
    expect(result.current.viewState).toMatchObject(DRAGGED);
    rerender({ fitKey: "full-view", frame: FULL, keepViewOf: "affected" });
    expect(result.current.viewState).toEqual(firstFull);
  });
});

describe("usablePadding", () => {
  it("passes a padding that leaves room through unchanged, and spreads a number to every side", () => {
    expect(usablePadding(1080, 752, { top: 16, right: 16, bottom: 160, left: 412 })).toEqual({
      top: 16,
      right: 16,
      bottom: 160,
      left: 412,
    });
    expect(usablePadding(1080, 752)).toEqual({ top: 12, right: 12, bottom: 12, left: 12 });
  });

  it("gives back in proportion so the frame always keeps 40 % of the map", () => {
    // The console at 1366 x 768 with the replay panel open: 412 + 392 of a 1006 px map.
    const p = usablePadding(1006, 620, { top: 16, right: 392, bottom: 160, left: 412 });
    expect(1006 - p.left - p.right).toBeCloseTo(1006 * 0.4, 6);
    expect(p.left / p.right).toBeCloseTo(412 / 392, 6);
    expect(p.top).toBe(16);
    expect(p.bottom).toBe(160);
  });

  it("moves the fit off the padded side", () => {
    observed = { width: 1080, height: 752 };
    const even = mount({}).result.current.viewState;
    const cleared = mount({ fitPadding: { top: 16, right: 16, bottom: 160, left: 412 } }).result
      .current.viewState;
    // The frame lands right of the column and above the scrub card: the centre moves west and
    // south of the frame's own centre, and the zoom drops to fit the smaller room.
    expect(cleared.longitude).toBeLessThan(even.longitude);
    expect(cleared.latitude).toBeLessThan(even.latitude);
    expect(cleared.zoom).toBeLessThan(even.zoom);
  });
});
