"use client";

import { create } from "zustand";

import { addMinutesIso, formatIstDate, formatIstTime, formatLead, formatValidTime } from "./time";

/** Replay speeds offered by the time bar and the replay panel (SPEC.md section 7.2). */
export const REPLAY_SPEEDS = [1, 10, 30, 60] as const;
export type ReplaySpeed = (typeof REPLAY_SPEEDS)[number];

/** Baked runs are served from the bake; live runs compute each cycle. */
export type ReplayMode = "baked" | "live";

/** Scrub window: -60 min observed to +180 min forecast, in 15-min ticks with 5-min fine steps. */
export const LEAD_MIN = -60;
export const LEAD_MAX = 180;
export const LEAD_TICK = 15;
export const LEAD_FINE = 5;
export const LEAD_COARSE = 60;

export const DEFAULT_BUNDLE_ID = "MUM-2019-07-02";
export const DEFAULT_SIM_TIME = "2019-07-02T06:40:00+05:30";
/** The 08:40 IST cycle: the 2 July storm near its peak, where the replay opens without `?bundle=`. */
export const REPLAY_PEAK_SIM_TIME = "2019-07-02T08:40:00+05:30";
export const DEFAULT_T0 = "2019-07-02T05:40:00+05:30";
export const DEFAULT_T1 = "2019-07-02T09:40:00+05:30";
export const DEFAULT_SPEED: ReplaySpeed = 30;

/** The cycle period (SPEC.md 11.11): the replay clock triggers a cycle every 5 sim-minutes. */
export const CYCLE_STEP_MIN = 5;

/** Minutes from `t0` to `iso`, fractional; 0 when either does not parse. */
export function minutesFromStart(iso: string, t0: string): number {
  const t = Date.parse(iso);
  const a = Date.parse(t0);
  if (Number.isNaN(t) || Number.isNaN(a)) return 0;
  return (t - a) / 60_000;
}

/**
 * The cycle boundary before (`-1`) or after (`1`) `iso`, on the 5-minute grid from `t0`.
 *
 * The seek buttons step cycle to cycle. They used to add five minutes to wherever the clock had
 * stopped, so a clock paused at 06:43:09 stepped to 06:48:09 and never landed on a cycle again.
 */
export function stepCycle(iso: string, t0: string, direction: -1 | 1): string {
  const minutes = minutesFromStart(iso, t0);
  const onGrid = minutes / CYCLE_STEP_MIN;
  // A hair either side of a boundary is on it: 06:40:00.0004 must step to 06:45, not 06:40.
  const next =
    direction > 0
      ? (Math.floor(onGrid + 1e-6) + 1) * CYCLE_STEP_MIN
      : (Math.ceil(onGrid - 1e-6) - 1) * CYCLE_STEP_MIN;
  return addMinutesIso(t0, next);
}

/** Clamps a lead to the scrub window and snaps it to whole minutes. */
export function clampLead(leadMin: number): number {
  if (!Number.isFinite(leadMin)) return 0;
  return Math.min(LEAD_MAX, Math.max(LEAD_MIN, Math.round(leadMin)));
}

/** Next speed in the 1x, 10x, 30x, 60x cycle. */
export function nextSpeed(speed: ReplaySpeed): ReplaySpeed {
  const i = REPLAY_SPEEDS.indexOf(speed);
  return REPLAY_SPEEDS[(i + 1) % REPLAY_SPEEDS.length] ?? DEFAULT_SPEED;
}

export function isReplaySpeed(value: number): value is ReplaySpeed {
  return (REPLAY_SPEEDS as readonly number[]).includes(value);
}

/** The slice of `GET /v1/replay/clock` the store follows (SPEC.md section 12). */
export interface ReplayClockSnapshot {
  bundle_id: string;
  sim_time: string;
  playing: boolean;
  speed: number;
  t0: string;
  t1: string;
  cycle_index: number;
  n_cycles?: number | null;
  mode?: string | null;
  last_run_id?: string | null;
  note?: string | null;
}

export interface ReplayState {
  bundleId: string;
  /** Replay clock: the cycle time the console is showing, ISO 8601 with +05:30. */
  simTime: string;
  playing: boolean;
  speed: ReplaySpeed;
  /** Scrub offset from the cycle time in minutes, -60 to 180. */
  leadMin: number;
  /** Bundle window (IST ISO). */
  t0: string;
  t1: string;
  cycleIndex: number;
  mode: ReplayMode;
  /** Cycles in the bundle, from the clock; null until the API has answered. */
  nCycles: number | null;
  /** The run the clock last published, or null while nothing is baked. */
  lastRunId: string | null;
  /** What the clock says about itself, already written as UI copy; null when all is well. */
  note: string | null;
  /** True once the API's clock has answered: the controls then drive the real replay. */
  fromApi: boolean;

  setBundle: (bundleId: string, window?: { t0: string; t1: string; simTime?: string }) => void;
  setSimTime: (iso: string) => void;
  play: () => void;
  pause: () => void;
  togglePlaying: () => void;
  setSpeed: (speed: ReplaySpeed) => void;
  cycleSpeed: () => void;
  setLeadMin: (leadMin: number) => void;
  /** Moves the scrub by a delta in minutes, clamped to the window. */
  stepLead: (deltaMin: number) => void;
  /** Seeks the replay clock; clamps to the bundle window when it is known. */
  seek: (iso: string) => void;
  setCycleIndex: (index: number) => void;
  setMode: (mode: ReplayMode) => void;
  /** Adopts the clock the API (or a `replay.clock` event) reports. */
  applyClock: (clock: ReplayClockSnapshot) => void;
  reset: () => void;
}

const initialState = {
  bundleId: DEFAULT_BUNDLE_ID,
  simTime: DEFAULT_SIM_TIME,
  playing: false,
  speed: DEFAULT_SPEED,
  leadMin: 0,
  t0: DEFAULT_T0,
  t1: DEFAULT_T1,
  cycleIndex: 0,
  mode: "baked" as ReplayMode,
  nCycles: null,
  lastRunId: null,
  note: null,
  fromApi: false,
};

function clampToWindow(iso: string, t0: string, t1: string): string {
  const t = Date.parse(iso);
  const a = Date.parse(t0);
  const b = Date.parse(t1);
  if (Number.isNaN(t)) return iso;
  if (!Number.isNaN(a) && t < a) return t0;
  if (!Number.isNaN(b) && t > b) return t1;
  return iso;
}

export const useReplayStore = create<ReplayState>()((set, get) => ({
  ...initialState,

  setBundle: (bundleId, window) =>
    set((s) => ({
      bundleId,
      t0: window?.t0 ?? s.t0,
      t1: window?.t1 ?? s.t1,
      simTime: window?.simTime ?? window?.t0 ?? s.simTime,
      cycleIndex: 0,
      leadMin: 0,
      playing: false,
    })),
  setSimTime: (iso) => set({ simTime: iso }),
  play: () => set({ playing: true }),
  pause: () => set({ playing: false }),
  togglePlaying: () => set((s) => ({ playing: !s.playing })),
  setSpeed: (speed) => set({ speed: isReplaySpeed(speed) ? speed : DEFAULT_SPEED }),
  cycleSpeed: () => set((s) => ({ speed: nextSpeed(s.speed) })),
  setLeadMin: (leadMin) => set({ leadMin: clampLead(leadMin) }),
  stepLead: (deltaMin) => set((s) => ({ leadMin: clampLead(s.leadMin + deltaMin) })),
  seek: (iso) => {
    const { t0, t1 } = get();
    set({ simTime: clampToWindow(iso, t0, t1) });
  },
  setCycleIndex: (index) => set({ cycleIndex: Math.max(0, Math.floor(index)) }),
  setMode: (mode) => set({ mode }),
  applyClock: (clock) =>
    set((s) => ({
      bundleId: clock.bundle_id,
      t0: clock.t0,
      t1: clock.t1,
      simTime: clampToWindow(clock.sim_time, clock.t0, clock.t1),
      playing: clock.playing,
      speed: isReplaySpeed(clock.speed) ? clock.speed : s.speed,
      cycleIndex: Math.max(0, Math.floor(clock.cycle_index)),
      mode: clock.mode === "live" ? "live" : "baked",
      nCycles: clock.n_cycles ?? null,
      lastRunId: clock.last_run_id ?? null,
      note: clock.note ?? null,
      fromApi: true,
    })),
  reset: () => set({ ...initialState }),
}));

/* Select-friendly derived helpers. Use as `useReplayStore(selectSimTimeLabel)`. */

/** "17:40 IST": the replay clock. */
export const selectSimTimeLabel = (s: Pick<ReplayState, "simTime">): string =>
  `${formatIstTime(s.simTime)} IST`;

/** "2 Jul 2019": the replay day. */
export const selectSimDateLabel = (s: Pick<ReplayState, "simTime">): string =>
  formatIstDate(s.simTime);

/** ISO valid time at the scrub position (cycle time plus lead). */
export const selectValidTime = (s: Pick<ReplayState, "simTime" | "leadMin">): string =>
  addMinutesIso(s.simTime, s.leadMin);

/** "18:20 (+40 min)": the scrub position as the copy rules want it. */
export const selectValidTimeLabel = (s: Pick<ReplayState, "simTime" | "leadMin">): string =>
  formatValidTime(s.simTime, s.leadMin);

/** "+40 min". */
export const selectLeadLabel = (s: Pick<ReplayState, "leadMin">): string => formatLead(s.leadMin);

/** Convenience for components that want the label without a selector. */
export function simTimeLabel(): string {
  return selectSimTimeLabel(useReplayStore.getState());
}
