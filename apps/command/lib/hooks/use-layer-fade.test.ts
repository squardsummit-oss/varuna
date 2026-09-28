import { act, renderHook } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { DUR_MS } from "@/lib/motion";

import { useLayerFade, type LayerFadeOptions } from "./use-layer-fade";

/**
 * motion's `useReducedMotion` reads matchMedia once per module graph, so this file asks the OS
 * question with "reduce" set from the start. Every test that is about the fade itself passes
 * `reduced: false` explicitly; one test leaves it out to check the OS preference is honoured.
 */
const originalMatchMedia = window.matchMedia;
beforeAll(() => {
  window.matchMedia = ((query: string) => ({
    matches: query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
});
afterAll(() => {
  window.matchMedia = originalMatchMedia;
  vi.unstubAllGlobals();
});

let pending = new Map<number, FrameRequestCallback>();
beforeEach(() => {
  pending = new Map();
  let nextId = 1;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    const id = nextId++;
    pending.set(id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => pending.delete(id));
});

function frame(now: number) {
  const callbacks = [...pending.values()];
  pending.clear();
  act(() => {
    for (const callback of callbacks) callback(now);
  });
}

type Props = { ready: boolean; options?: LayerFadeOptions };

function render(initial: Props) {
  return renderHook(({ ready, options }: Props) => useLayerFade("buildings", ready, options), {
    initialProps: initial,
  });
}

describe("useLayerFade (motion M19)", () => {
  it("is 0 until the layer is ready", () => {
    const { result } = render({ ready: false, options: { reduced: false } });
    expect(result.current).toBe(0);
    expect(pending.size).toBe(0);
  });

  it("fades from 0 to 1 over the catalogue's 400 ms, monotonically", () => {
    expect(DUR_MS.layerFade).toBe(400);
    const options: LayerFadeOptions = { reduced: false };
    const { result, rerender } = render({ ready: false, options });
    rerender({ ready: true, options });
    expect(result.current).toBe(0);

    let previous = 0;
    for (const now of [0, 50, 100, 200, 300, 399]) {
      frame(now);
      expect(result.current).toBeGreaterThanOrEqual(previous);
      expect(result.current).toBeLessThan(1);
      previous = result.current;
    }
    frame(400);
    expect(result.current).toBe(1);
    expect(pending.size).toBe(0);
  });

  it("starts at 1 for a layer already ready at mount, so a reopened screen does not replay", () => {
    const { result } = render({ ready: true, options: { reduced: false } });
    expect(result.current).toBe(1);
    expect(pending.size).toBe(0);
  });

  it("drops to 0 when the layer goes away and fades in again when it returns", () => {
    const options: LayerFadeOptions = { reduced: false };
    const { result, rerender } = render({ ready: true, options });
    rerender({ ready: false, options });
    expect(result.current).toBe(0);
    rerender({ ready: true, options });
    expect(result.current).toBe(0);
    frame(0);
    frame(400);
    expect(result.current).toBe(1);
  });

  it("honours the OS reduced-motion preference: 1 on the first ready render, no frames", () => {
    const { result, rerender } = render({ ready: false });
    expect(result.current).toBe(0);
    rerender({ ready: true });
    expect(result.current).toBe(1);
    expect(pending.size).toBe(0);
  });
});
