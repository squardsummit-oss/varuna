"use client";

import { useCallback, useSyncExternalStore } from "react";

/** Breakpoints the layouts care about (SPEC.md section 6.5 and 7.11). */
export const MEDIA = {
  /** Public map and report flow: one column, bottom sheet. */
  mobile: "(max-width: 767px)",
  /** The 1366 x 768 laptop the console must still fit. */
  narrowConsole: "(max-width: 1365px)",
  /** 4K wall at 150 % zoom. */
  wall: "(min-width: 2400px)",
  reducedMotion: "(prefers-reduced-motion: reduce)",
  coarsePointer: "(pointer: coarse)",
} as const;

/**
 * Tracks a media query. `serverValue` is what the server render assumes (default false) so the
 * first client render matches; the real value arrives on hydration.
 */
export function useMediaQuery(query: string, serverValue = false): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );
  const getSnapshot = useCallback(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return serverValue;
    return window.matchMedia(query).matches;
  }, [query, serverValue]);
  return useSyncExternalStore(subscribe, getSnapshot, () => serverValue);
}

/** True below 768 px: the public map and report flow layouts. */
export function useIsMobile(): boolean {
  return useMediaQuery(MEDIA.mobile);
}

/** True when the OS asks for reduced motion; the CSS-level twin of `useMotionPref`. */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery(MEDIA.reducedMotion);
}
