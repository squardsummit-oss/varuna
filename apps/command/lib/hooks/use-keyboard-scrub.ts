"use client";

import { useCallback, type KeyboardEvent as ReactKeyboardEvent } from "react";

import {
  LEAD_COARSE,
  LEAD_FINE,
  LEAD_MAX,
  LEAD_MIN,
  LEAD_TICK,
  useReplayStore,
} from "@/lib/stores/replay";

export interface KeyboardScrubOptions {
  /** Set false to ignore keys (e.g. while a dialog is open). */
  enabled?: boolean;
  /** Called after the scrub moves, with the new lead in minutes. */
  onStep?: (leadMin: number) => void;
  /** Called after Space toggles play. */
  onToggle?: (playing: boolean) => void;
}

export interface KeyboardScrub {
  /** Attach to the focusable scrub element (the time bar track, the replay seek). */
  onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => void;
  /** Moves the scrub by a delta in minutes, clamped to the window. */
  step: (deltaMin: number) => void;
  /** Jumps to a lead in minutes. */
  seekTo: (leadMin: number) => void;
  /** ARIA slider attributes for the element that owns the keys. */
  ariaProps: {
    role: "slider";
    tabIndex: 0;
    "aria-valuemin": number;
    "aria-valuemax": number;
    "aria-valuenow": number;
    "aria-valuetext": string;
    "aria-label": string;
  };
}

/** Step size for an arrow key: 60 min with Shift, 5 min with Ctrl or Cmd, otherwise 15 (SPEC.md 6.10). */
export function scrubStep(event: Pick<KeyboardEvent, "shiftKey" | "ctrlKey" | "metaKey">): number {
  if (event.shiftKey) return LEAD_COARSE;
  if (event.ctrlKey || event.metaKey) return LEAD_FINE;
  return LEAD_TICK;
}

/**
 * Element-scoped keyboard scrubbing for the time bar and the replay panel. The global shortcuts in
 * `lib/shortcuts.ts` also handle arrows on the window; this hook stops propagation so a focused
 * slider steps exactly once. Home and End jump to -60 and +180 min.
 */
export function useKeyboardScrub(options: KeyboardScrubOptions = {}): KeyboardScrub {
  const { enabled = true, onStep, onToggle } = options;
  const leadMin = useReplayStore((s) => s.leadMin);
  const playing = useReplayStore((s) => s.playing);

  const step = useCallback(
    (deltaMin: number) => {
      const store = useReplayStore.getState();
      store.stepLead(deltaMin);
      onStep?.(useReplayStore.getState().leadMin);
    },
    [onStep],
  );

  const seekTo = useCallback(
    (target: number) => {
      const store = useReplayStore.getState();
      store.setLeadMin(target);
      onStep?.(useReplayStore.getState().leadMin);
    },
    [onStep],
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>) => {
      if (!enabled) return;
      switch (event.key) {
        case "ArrowLeft":
        case "ArrowDown":
          event.preventDefault();
          event.stopPropagation();
          step(-scrubStep(event));
          return;
        case "ArrowRight":
        case "ArrowUp":
          event.preventDefault();
          event.stopPropagation();
          step(scrubStep(event));
          return;
        case "Home":
          event.preventDefault();
          event.stopPropagation();
          seekTo(LEAD_MIN);
          return;
        case "End":
          event.preventDefault();
          event.stopPropagation();
          seekTo(LEAD_MAX);
          return;
        case " ": {
          event.preventDefault();
          event.stopPropagation();
          const store = useReplayStore.getState();
          store.togglePlaying();
          onToggle?.(useReplayStore.getState().playing);
          return;
        }
        default:
      }
    },
    [enabled, onToggle, seekTo, step],
  );

  const sign = leadMin < 0 ? "-" : "+";
  return {
    onKeyDown,
    step,
    seekTo,
    ariaProps: {
      role: "slider",
      tabIndex: 0,
      "aria-valuemin": LEAD_MIN,
      "aria-valuemax": LEAD_MAX,
      "aria-valuenow": leadMin,
      "aria-valuetext": `${sign}${Math.abs(leadMin)} min${playing ? ", playing" : ""}`,
      "aria-label": "Scrub the forecast time",
    },
  };
}
