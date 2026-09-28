"use client";

import { useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { easeUi } from "@/lib/motion";

/** What restarts a tween. `null` means there is nothing to animate, so the tween reads finished. */
export type TweenKey = string | number | null;

export interface TweenOptions {
  /** Maps linear time 0 to 1 onto progress; defaults to the catalogue easing (SPEC.md 8). */
  easing?: (t: number) => number;
  /**
   * Overrides the OS preference. Leave it out and the hook asks `useReducedMotion`, the same
   * question `useMotionPref` asks, so every motion agrees on what the user wants.
   */
  reduced?: boolean;
  /**
   * Whether the key present at mount animates. Off by default: a screen reopened on a finished
   * state should show it finished rather than replay the motion that led there.
   */
  animateOnMount?: boolean;
}

interface Frame {
  key: TweenKey;
  progress: number;
}

/**
 * Progress from 0 to 1 over `durationMs`, eased, restarting every time `key` changes. The shared
 * clock for rAF-driven motion in the catalogue that framer cannot reach: deck.gl layer opacity
 * (M19), route draw-on (M14), isochrone morphs (M15).
 *
 * - A key change reads 0 on the very render it happens, so the new state never flashes at full
 *   strength before the first frame lands.
 * - Under reduced motion it returns 1 on every render: the end value, with no frames scheduled.
 * - The animation frame is cancelled on unmount and on every key change.
 *
 * Each frame is a React render of the calling component, so keep what that component passes to
 * heavy children (deck.gl data, accessors) memoised, and let only the progress-derived value
 * change.
 */
export function useTween(key: TweenKey, durationMs: number, options: TweenOptions = {}): number {
  const { easing = easeUi, reduced: reducedOption, animateOnMount = false } = options;
  const prefersReduced = useReducedMotion() === true;
  const reduced = reducedOption ?? prefersReduced;

  const [frame, setFrame] = useState<Frame>(() => ({
    key,
    progress: animateOnMount && key !== null ? 0 : 1,
  }));

  // Adjusting state while rendering, the React-documented way to reset on a prop change: the
  // render that sees a new key already reads 0 rather than the previous key's 1.
  let current = frame;
  if (frame.key !== key) {
    current = { key, progress: key === null ? 1 : 0 };
    setFrame(current);
  }

  // The key present at mount, when it must not animate. Cleared as soon as the key moves, so the
  // same key coming back later (a layer hidden and shown again) animates like any other change.
  const skipKey = useRef<{ key: TweenKey } | null>(animateOnMount ? null : { key });

  const easingRef = useRef(easing);
  useEffect(() => {
    easingRef.current = easing;
  }, [easing]);

  useEffect(() => {
    if (key === null) {
      skipKey.current = null;
      return;
    }
    if (skipKey.current !== null && skipKey.current.key === key) return;
    skipKey.current = null;
    // Nothing to schedule: the render returns the end value for both.
    if (reduced || durationMs <= 0) return;

    let handle = 0;
    let started: number | null = null;
    const tick = (now: number) => {
      if (started === null) started = now;
      const t = Math.min((now - started) / durationMs, 1);
      setFrame({ key, progress: t >= 1 ? 1 : easingRef.current(t) });
      if (t < 1) handle = requestAnimationFrame(tick);
    };
    handle = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(handle);
  }, [key, durationMs, reduced]);

  if (reduced || key === null || durationMs <= 0) return 1;
  return current.progress;
}
