import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { easeUi } from "@/lib/motion";

import { useTween, type TweenKey, type TweenOptions } from "./use-tween";

/** A hand-driven animation-frame clock: nothing runs until `frame(now)` is called. */
function installFrameClock() {
  let nextId = 1;
  const pending = new Map<number, FrameRequestCallback>();
  const cancelled: number[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    const id = nextId++;
    pending.set(id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    cancelled.push(id);
    pending.delete(id);
  });
  return {
    pendingCount: () => pending.size,
    cancelled,
    frame(now: number) {
      const callbacks = [...pending.values()];
      pending.clear();
      act(() => {
        for (const callback of callbacks) callback(now);
      });
    },
  };
}

type Props = { tweenKey: TweenKey; duration: number; options?: TweenOptions };

function render(initial: Props) {
  return renderHook(
    ({ tweenKey, duration, options }: Props) => useTween(tweenKey, duration, options),
    {
      initialProps: initial,
    },
  );
}

describe("useTween", () => {
  let clock: ReturnType<typeof installFrameClock>;

  beforeEach(() => {
    clock = installFrameClock();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads finished for the key present at mount and schedules nothing", () => {
    const { result } = render({ tweenKey: "roads", duration: 300, options: { reduced: false } });
    expect(result.current).toBe(1);
    expect(clock.pendingCount()).toBe(0);
  });

  it("animates the mount key when asked, on the catalogue easing, and stops at the duration", () => {
    const { result } = render({
      tweenKey: "roads",
      duration: 300,
      options: { reduced: false, animateOnMount: true },
    });
    expect(result.current).toBe(0);
    clock.frame(1000);
    expect(result.current).toBe(0);
    clock.frame(1150);
    expect(result.current).toBeCloseTo(easeUi(0.5), 10);
    clock.frame(1300);
    expect(result.current).toBe(1);
    expect(clock.pendingCount()).toBe(0);
  });

  it("reads 0 on the render where the key changes, then rises monotonically to 1", () => {
    const options: TweenOptions = { reduced: false };
    const { result, rerender } = render({ tweenKey: "roads", duration: 400, options });
    rerender({ tweenKey: "drains", duration: 400, options });
    expect(result.current).toBe(0);

    let previous = 0;
    for (const now of [0, 40, 100, 200, 300, 399]) {
      clock.frame(now);
      expect(result.current).toBeGreaterThanOrEqual(previous);
      expect(result.current).toBeLessThan(1);
      previous = result.current;
    }
    clock.frame(400);
    expect(result.current).toBe(1);
  });

  it("restarts from 0 and cancels the running frame when the key changes mid-tween", () => {
    const options: TweenOptions = { reduced: false };
    const { result, rerender } = render({ tweenKey: "a", duration: 400, options });
    rerender({ tweenKey: "b", duration: 400, options });
    clock.frame(0);
    clock.frame(200);
    expect(result.current).toBeGreaterThan(0);
    const cancelledBefore = clock.cancelled.length;

    rerender({ tweenKey: "c", duration: 400, options });
    expect(result.current).toBe(0);
    expect(clock.cancelled.length).toBeGreaterThan(cancelledBefore);
    expect(clock.pendingCount()).toBe(1);
  });

  it("animates a key that comes back after going away", () => {
    const options: TweenOptions = { reduced: false };
    const { result, rerender } = render({ tweenKey: "roads", duration: 400, options });
    rerender({ tweenKey: null, duration: 400, options });
    expect(result.current).toBe(1);
    rerender({ tweenKey: "roads", duration: 400, options });
    expect(result.current).toBe(0);
    expect(clock.pendingCount()).toBe(1);
  });

  it("jumps to the end value under reduced motion without scheduling a frame", () => {
    const options: TweenOptions = { reduced: true };
    const { result, rerender } = render({ tweenKey: "roads", duration: 400, options });
    rerender({ tweenKey: "drains", duration: 400, options });
    expect(result.current).toBe(1);
    expect(clock.pendingCount()).toBe(0);
  });

  it("treats a zero duration as already finished", () => {
    const options: TweenOptions = { reduced: false };
    const { result, rerender } = render({ tweenKey: "a", duration: 0, options });
    rerender({ tweenKey: "b", duration: 0, options });
    expect(result.current).toBe(1);
    expect(clock.pendingCount()).toBe(0);
  });

  it("uses a caller's easing", () => {
    const linear = (t: number) => t;
    const { result } = render({
      tweenKey: "a",
      duration: 200,
      options: { reduced: false, animateOnMount: true, easing: linear },
    });
    clock.frame(0);
    clock.frame(50);
    expect(result.current).toBeCloseTo(0.25, 10);
  });

  it("cancels its frame on unmount", () => {
    const options: TweenOptions = { reduced: false };
    const { rerender, unmount } = render({ tweenKey: "a", duration: 400, options });
    rerender({ tweenKey: "b", duration: 400, options });
    expect(clock.pendingCount()).toBe(1);
    unmount();
    expect(clock.pendingCount()).toBe(0);
  });
});
