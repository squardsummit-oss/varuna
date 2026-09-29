"use client";

import { create } from "zustand";

/**
 * Forecast playback on the console: Play steps the map through the loaded run's 5-minute steps
 * (motion M7). The scrub position itself is the replay store's `leadMin`, which the time bar's
 * slider and the arrow keys already write, so there is one position and one Play for the map.
 *
 * Playback never touches the API's shared replay clock. That clock is shared by every viewer of
 * the deployment, and a judge pressing Play on the console used to move it for everyone while the
 * map beside the button stood still. Smriti owns the replay clock; the console owns this.
 */

/** Forecast steps per second. */
export const PLAY_RATES = [1, 3, 6] as const;
export type PlayRate = (typeof PLAY_RATES)[number];
export const DEFAULT_PLAY_RATE: PlayRate = 3;

export const PLAY_RATE_LABELS: Record<PlayRate, string> = {
  1: "Slow",
  3: "Normal",
  6: "Fast",
};

export function isPlayRate(value: number): value is PlayRate {
  return (PLAY_RATES as readonly number[]).includes(value);
}

/** Milliseconds between steps at a rate. */
export function playIntervalMs(rate: PlayRate): number {
  return Math.round(1000 / rate);
}

export interface ScrubState {
  playing: boolean;
  rate: PlayRate;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  setRate: (rate: PlayRate) => void;
}

export const useScrubStore = create<ScrubState>()((set) => ({
  playing: false,
  rate: DEFAULT_PLAY_RATE,
  play: () => set({ playing: true }),
  pause: () => set({ playing: false }),
  toggle: () => set((s) => ({ playing: !s.playing })),
  setRate: (rate) => set({ rate: isPlayRate(rate) ? rate : DEFAULT_PLAY_RATE }),
}));

/**
 * The step whose lead is nearest `lead`, or 0 for a run with no steps. `leads` are minutes from
 * the cycle to each step's valid time, ascending (the 06:40 cycle's are 5, 10, ... 180).
 */
export function nearestStep(leads: readonly number[], lead: number): number {
  if (leads.length === 0) return 0;
  let best = 0;
  let bestGap = Math.abs(leads[0] - lead);
  for (let i = 1; i < leads.length; i += 1) {
    const gap = Math.abs(leads[i] - lead);
    if (gap < bestGap) {
      best = i;
      bestGap = gap;
    }
  }
  return best;
}

/** `lead` snapped to the run's nearest step lead; unchanged when the run has no steps. */
export function snapLead(leads: readonly number[], lead: number): number {
  if (leads.length === 0) return lead;
  return leads[nearestStep(leads, lead)];
}
