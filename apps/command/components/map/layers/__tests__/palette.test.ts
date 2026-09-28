/**
 * The map's pure colour functions, pinned to the values the monolith `city-map.tsx` returned before
 * MO1 moved them into `layers/palette.ts`.
 */

import { describe, expect, it } from "vitest";

import {
  CAUTION,
  DIFF_DEADBAND_CM,
  DIFF_FULL_CM,
  DIFF_UNCHANGED,
  IMPASSABLE,
  PASSABLE,
  diffColour,
  drainColour,
  passabilityRgba,
} from "../palette";

describe("passabilityRgba", () => {
  it("is impassable at and above the vehicle's depth", () => {
    expect(passabilityRgba(30, 30)).toEqual(IMPASSABLE);
    expect(passabilityRgba(80, 30)).toEqual(IMPASSABLE);
  });

  it("is caution from half the vehicle's depth", () => {
    expect(passabilityRgba(15, 30)).toEqual(CAUTION);
    expect(passabilityRgba(29.9, 30)).toEqual(CAUTION);
  });

  it("is passable below half", () => {
    expect(passabilityRgba(14.9, 30)).toEqual(PASSABLE);
    expect(passabilityRgba(0, 30)).toEqual(PASSABLE);
  });

  it("keeps the monolith's colours", () => {
    expect(PASSABLE).toEqual([59, 130, 246, 235]);
    expect(CAUTION).toEqual([249, 115, 22, 245]);
    expect(IMPASSABLE).toEqual([185, 28, 28, 255]);
  });
});

describe("diffColour", () => {
  it("draws a change under the 0.5 cm deadband as unchanged", () => {
    expect(DIFF_DEADBAND_CM).toBe(0.5);
    expect(diffColour(undefined)).toEqual(DIFF_UNCHANGED);
    expect(diffColour(0.49)).toEqual(DIFF_UNCHANGED);
    expect(diffColour(-0.49)).toEqual(DIFF_UNCHANGED);
    expect(DIFF_UNCHANGED).toEqual([43, 58, 85, 190]);
  });

  it("is blue for less water and red for more, opacity by size", () => {
    expect(diffColour(-0.5)).toEqual([59, 130, 246, Math.round(90 + 165 * (0.5 / 20))]);
    expect(diffColour(10)).toEqual([239, 68, 68, Math.round(90 + 165 * 0.5)]);
  });

  it("saturates at 20 cm", () => {
    expect(DIFF_FULL_CM).toBe(20);
    expect(diffColour(-20)).toEqual([59, 130, 246, 255]);
    expect(diffColour(-75)).toEqual([59, 130, 246, 255]);
    expect(diffColour(40)).toEqual([239, 68, 68, 255]);
  });
});

describe("drainColour", () => {
  it("bands blockage at 0.25, 0.5 and 0.75, exclusive of the edge", () => {
    expect(drainColour(0)).toEqual([62, 76, 110, 200]);
    expect(drainColour(0.25)).toEqual([62, 76, 110, 200]);
    expect(drainColour(0.26)).toEqual([124, 58, 237, 200]);
    expect(drainColour(0.5)).toEqual([124, 58, 237, 200]);
    expect(drainColour(0.51)).toEqual([192, 38, 211, 200]);
    expect(drainColour(0.75)).toEqual([192, 38, 211, 200]);
    expect(drainColour(0.76)).toEqual([232, 121, 249, 200]);
    expect(drainColour(1)).toEqual([232, 121, 249, 200]);
  });
});
