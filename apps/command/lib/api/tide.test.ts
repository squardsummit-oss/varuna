import { describe, expect, it } from "vitest";

import { tideFromSeries } from "./tide";

const POINT = { lon: 72.8, lat: 18.95, label: "off Colaba" };
const hour = (h: number) => new Date(Date.UTC(2026, 8, 29, 18 + h)).toISOString();

describe("tideFromSeries", () => {
  it("reads now, the next high and low, and whether the sea is rising", () => {
    const levels = [-0.5, 0.1, 0.8, 1.3, 1.1, 0.2, -0.9, -1.2, -0.7];
    const times = levels.map((_, i) => hour(i));
    const tide = tideFromSeries(times, levels, Date.parse(hour(1)) + 10 * 60_000, POINT);
    expect(tide?.now.m).toBe(0.1);
    expect(tide?.rising).toBe(true);
    expect(tide?.nextHigh).toEqual({ ts: hour(3), m: 1.3 });
    expect(tide?.nextLow).toEqual({ ts: hour(7), m: -1.2 });
  });

  it("skips missing hours and refuses a series too short to read", () => {
    expect(tideFromSeries([hour(0), hour(1)], [0.1, null], Date.parse(hour(0)), POINT)).toBeNull();
    const tide = tideFromSeries(
      [hour(0), hour(1), hour(2), hour(3)],
      [1.0, null, 0.5, 0.2],
      Date.parse(hour(2)),
      POINT,
    );
    expect(tide?.series).toHaveLength(3);
    expect(tide?.rising).toBe(false);
  });
});
