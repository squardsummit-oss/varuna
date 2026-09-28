/**
 * The hero's M1 loop and its readout (SPEC.md 7.1, section 8 M1).
 *
 * M1 "pauses on hover" and holds the +120 min frame only under reduced motion. Until 2026-09-22
 * a hover jumped to that still frame and resuming restarted at +0 min; these pin the fix.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { heroReadout, useScrubLoop } from "@/components/landing/hero";

let now = 0;
let queue: FrameRequestCallback[] = [];

/** Advance the fake animation clock by `ms`, one 16 ms frame at a time. */
function advance(ms: number) {
  const end = now + ms;
  while (now < end) {
    now += 16;
    const frames = queue;
    queue = [];
    for (const callback of frames) callback(now);
  }
}

beforeEach(() => {
  now = 0;
  queue = [];
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    queue.push(callback);
    return queue.length;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {
    queue = [];
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("M1 scrub loop", () => {
  it("holds the frame on screen while paused and carries on from it", () => {
    const { result, rerender } = renderHook(
      ({ playing }: { playing: boolean }) => useScrubLoop(playing, false),
      { initialProps: { playing: true } },
    );
    act(() => advance((8000 / 36) * 10 + 20));
    const paused = result.current;
    expect(paused).toBeGreaterThanOrEqual(9);

    rerender({ playing: false });
    act(() => advance(2000));
    expect(result.current).toBe(paused);

    rerender({ playing: true });
    act(() => advance(8000 / 36 + 20));
    expect(result.current).toBe(paused + 1);
  });

  it("shows the still +120 min frame only under reduced motion", () => {
    const { result } = renderHook(() => useScrubLoop(true, true));
    act(() => advance(3000));
    expect(result.current).toBe(24);
  });
});

describe("hero readout", () => {
  it("counts from the run's own cycle time, not from a written-in 06:40", () => {
    expect(heroReadout("2019-07-02T06:40:00+05:30", 8)).toBe("07:20 IST · +40 min");
    expect(heroReadout("2019-07-02T09:10:00+05:30", 0)).toBe("09:10 IST · +0 min");
  });

  it("says nothing rather than guess when the run has no cycle time", () => {
    expect(heroReadout(null, 4)).toBeNull();
  });
});
