import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_PLAY_RATE,
  PLAY_RATES,
  isPlayRate,
  nearestStep,
  playIntervalMs,
  snapLead,
  useScrubStore,
} from "./scrub";

/** The 06:40 cycle's leads: 5, 10, ... 180 minutes. */
const LEADS = Array.from({ length: 36 }, (_, i) => (i + 1) * 5);

afterEach(() => {
  useScrubStore.setState({ playing: false, rate: DEFAULT_PLAY_RATE });
});

describe("nearestStep", () => {
  it("puts +0, which no run has a step at, on the first step", () => {
    expect(nearestStep(LEADS, 0)).toBe(0);
  });

  it("finds the step a 15-minute arrow press lands on", () => {
    expect(nearestStep(LEADS, 20)).toBe(3);
    expect(nearestStep(LEADS, 180)).toBe(35);
  });

  it("clamps a lead past the last step onto it", () => {
    expect(nearestStep(LEADS, 400)).toBe(35);
    expect(nearestStep(LEADS, -60)).toBe(0);
  });

  it("answers 0 for a run with no steps", () => {
    expect(nearestStep([], 45)).toBe(0);
  });
});

describe("snapLead", () => {
  it("snaps a slider value onto a step the map can show", () => {
    expect(snapLead(LEADS, 47)).toBe(45);
    expect(snapLead(LEADS, -30)).toBe(5);
  });

  it("leaves the lead alone when there are no steps", () => {
    expect(snapLead([], 47)).toBe(47);
  });
});

describe("playback rate", () => {
  it("accepts only the rates the menu offers", () => {
    for (const rate of PLAY_RATES) expect(isPlayRate(rate)).toBe(true);
    expect(isPlayRate(30)).toBe(false);
  });

  it("turns steps a second into an interval", () => {
    expect(playIntervalMs(1)).toBe(1000);
    expect(playIntervalMs(3)).toBe(333);
    expect(playIntervalMs(6)).toBe(167);
  });
});

describe("useScrubStore", () => {
  it("plays, pauses and toggles", () => {
    const store = useScrubStore.getState();
    expect(useScrubStore.getState().playing).toBe(false);
    store.play();
    expect(useScrubStore.getState().playing).toBe(true);
    store.toggle();
    expect(useScrubStore.getState().playing).toBe(false);
    store.toggle();
    store.pause();
    expect(useScrubStore.getState().playing).toBe(false);
  });

  it("falls back to the default rate for one it does not offer", () => {
    useScrubStore.getState().setRate(6);
    expect(useScrubStore.getState().rate).toBe(6);
    useScrubStore.getState().setRate(30 as never);
    expect(useScrubStore.getState().rate).toBe(DEFAULT_PLAY_RATE);
  });
});
