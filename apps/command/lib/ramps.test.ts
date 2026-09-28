import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  DEPTH_THRESHOLDS_CM,
  MIN_PROBABILITY_OPACITY,
  bandFill,
  colors,
  depthBand,
  depthColor,
  depthLegendStops,
  depthRgba,
  drainColor,
  drainLegendStops,
  drainRgba,
  passability,
  passabilityStops,
  probabilityLegendStops,
  probabilityOpacity,
  probabilityRgba,
  rainLegendStops,
  rgbaCss,
} from "./ramps";

/** The rain group as committed, so the legend is checked against tokens.json and not against itself. */
const rainTokens = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../packages/tokens/tokens.json"), "utf8"),
).color.rain as Record<string, { value: string; label: string; meaning: string }>;

describe("depth ramp", () => {
  it("has the fixed thresholds from tokens.json", () => {
    expect([...DEPTH_THRESHOLDS_CM]).toEqual([5, 15, 30, 45, 60]);
  });
  it("maps lower bounds inclusively and negatives to dry", () => {
    expect(depthBand(4.9).key).toBe("dry");
    expect(depthBand(5).key).toBe("1");
    expect(depthBand(15).key).toBe("2");
    expect(depthBand(30).key).toBe("3");
    expect(depthBand(45).key).toBe("4");
    expect(depthBand(60).key).toBe("5");
    expect(depthBand(-3).key).toBe("dry");
    expect(depthBand(Number.NaN).key).toBe("dry");
  });
  it("returns the token hex for a depth", () => {
    expect(depthColor(55)).toBe(colors["depth-4"]);
    expect(depthColor(0)).toBe(colors["depth-dry"]);
  });
  it("lists six legend stops in ramp order with meanings", () => {
    const stops = depthLegendStops();
    expect(stops.map((s) => s.key)).toEqual([
      "depth-dry",
      "depth-1",
      "depth-2",
      "depth-3",
      "depth-4",
      "depth-5",
    ]);
    expect(stops[3]?.label).toBe("30-45 cm");
    expect(stops[3]?.meaning).toBe("cars impassable");
    expect(stops[3]?.cssVar).toBe("var(--depth-3)");
  });
});

describe("drain ramp", () => {
  it("colours beta by quartile with magenta at the top", () => {
    expect(drainColor(0.1)).toBe(colors["drain-0"]);
    expect(drainColor(0.3)).toBe(colors["drain-1"]);
    expect(drainColor(0.6)).toBe(colors["drain-2"]);
    expect(drainColor(0.9)).toBe(colors["drain-3"]);
  });
  it("lists four legend stops from clear to blocked", () => {
    const stops = drainLegendStops();
    expect(stops).toHaveLength(4);
    expect(stops[0]?.meaning).toBe("clear");
    expect(stops[3]?.meaning).toBe("blocked");
    expect(stops[3]?.label).toBe("> 0.75");
  });
  it("gives deck.gl rgba with alpha from opacity", () => {
    const [, , , a] = drainRgba(0.9, 0.5);
    expect(a).toBe(128);
  });
});

describe("rain ramp", () => {
  it("lists every rain band of tokens.json, in ramp order, with its label and colour", () => {
    const stops = rainLegendStops();
    const bands = Object.entries(rainTokens);
    expect(stops).toHaveLength(bands.length);
    expect(stops.map((s) => s.key)).toEqual(bands.map(([key]) => `rain-${key}`));
    expect(stops.map((s) => s.label)).toEqual(bands.map(([, band]) => band.label));
    expect(stops.map((s) => s.meaning)).toEqual(bands.map(([, band]) => band.meaning));
    expect(stops.map((s) => s.hex)).toEqual(bands.map(([, band]) => band.value));
    expect(stops[3]?.label).toBe("20-40 mm/h");
    expect(stops[3]?.cssVar).toBe("var(--rain-4)");
    expect(stops.every((s) => s.opacity === 1)).toBe(true);
  });
  it("never reuses a depth or drain colour, so an echo cannot read as a flooded street", () => {
    const water = new Set<string>([
      colors["depth-dry"],
      colors["depth-1"],
      colors["depth-2"],
      colors["depth-3"],
      colors["depth-4"],
      colors["depth-5"],
      colors["drain-0"],
      colors["drain-1"],
      colors["drain-2"],
      colors["drain-3"],
    ]);
    expect(rainLegendStops().some((s) => water.has(s.hex))).toBe(false);
  });
});

describe("probability mode", () => {
  it("never lets a segment vanish", () => {
    expect(probabilityOpacity(0)).toBe(MIN_PROBABILITY_OPACITY);
    expect(probabilityOpacity(0.05)).toBe(MIN_PROBABILITY_OPACITY);
    expect(probabilityOpacity(0.82)).toBeCloseTo(0.82);
    expect(probabilityOpacity(1.4)).toBe(1);
  });
  it("keeps the depth colour and puts P in the alpha channel", () => {
    const [r, g, b, a] = probabilityRgba(55, 0.5);
    const [dr, dg, db] = depthRgba(55);
    expect([r, g, b]).toEqual([dr, dg, db]);
    expect(a).toBe(128);
    expect(probabilityRgba(55, 0)[3]).toBe(Math.round(MIN_PROBABILITY_OPACITY * 255));
  });
  it("builds a probability legend at the threshold's colour with rising opacity", () => {
    const stops = probabilityLegendStops(30);
    expect(stops).toHaveLength(5);
    expect(stops.every((s) => s.hex === colors["depth-3"])).toBe(true);
    expect(stops[0]?.opacity).toBe(MIN_PROBABILITY_OPACITY);
    expect(stops[4]?.opacity).toBe(1);
    expect(stops[2]?.meaning).toBe("P(depth > 30 cm)");
  });
});

describe("public-map passability", () => {
  it("uses the profile threshold and half of it for caution", () => {
    const car = passabilityStops("car");
    expect(car.map((s) => s.state)).toEqual(["passable", "caution", "impassable"]);
    expect(car[0]?.range).toBe("below 15 cm");
    expect(car[1]?.range).toBe("15-30 cm");
    expect(car[2]?.range).toBe("30 cm and above");
    expect(car[0]?.hex).toBe(colors.tide);
    expect(car[1]?.hex).toBe(colors["depth-2"]);
    expect(car[2]?.hex).toBe(colors["depth-3"]);
  });
  it("never lets caution start below the 5 cm floor", () => {
    expect(passabilityStops("two-wheeler")[0]?.range).toBe("below 7.5 cm");
    expect(passabilityStops("pedestrian")[1]?.range).toBe("15-30 cm");
  });
  it("classifies a depth for a profile", () => {
    expect(passability(10, "car").state).toBe("passable");
    expect(passability(20, "car").state).toBe("caution");
    expect(passability(30, "car").state).toBe("impassable");
    expect(passability(50, "ambulance").state).toBe("caution");
    expect(passability(60, "ambulance").state).toBe("impassable");
    expect(passability(Number.NaN, "bus").state).toBe("passable");
  });
});

describe("css helpers", () => {
  it("writes modern rgb syntax with a clamped alpha", () => {
    expect(rgbaCss(colors.tide, 0.5)).toBe("rgb(45 212 191 / 0.5)");
    expect(rgbaCss(colors.tide, 4)).toBe("rgb(45 212 191 / 1)");
  });
  it("fills chart bands at the token opacity", () => {
    expect(bandFill(colors["chart-2"])).toBe("rgb(96 165 250 / 0.2)");
  });
});
