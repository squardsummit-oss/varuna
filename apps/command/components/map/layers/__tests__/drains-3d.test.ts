/**
 * The drain X-ray's pure parts: the invert profile along an uneven polyline, the cover
 * exaggeration, the culling, the named unavailable state and the copy.
 *
 * jsdom has no WebGL, so what is checked of the layers themselves is the props deck is handed -
 * the accessors, the units and the extension - by calling the accessors the way deck would. The
 * expected z values are arithmetic done here from the fixture, not copied from the implementation.
 */

import { describe, expect, it } from "vitest";

import type { Bbox } from "../../basemap";
import { DASHED, DRAIN_DASH } from "../drains";
import {
  drainXraySummary,
  drains3dLayers,
  drawnInvert,
  exaggerationLabel,
  isAdverse,
  PIPE_MIN_PIXELS,
  pipeProfile,
  SHAFT_COLOUR,
  SHAFT_MIN_ZOOM,
  selectOutfalls,
  selectPipes,
  selectShafts,
  TIDAL_OUTFALL_COLOUR,
  viewKey,
  type Drains3dReady,
} from "../drains-3d";
import { drainColour, SURCHARGE_LINE } from "../palette";
import type { DrainNode, DrainPath } from "../types";

/** A lon/lat box covering the whole fixture city. */
const ALL: Bbox = [
  [72.8, 18.99],
  [72.92, 19.14],
];

function edge(over: Partial<DrainPath> = {}): DrainPath {
  return {
    id: "MUM-E000000",
    path: [
      [72.82, 19.0],
      [72.821, 19.0],
    ],
    beta: 0.2,
    diameter: 0.6,
    zUpM: 8.32,
    zDnM: 7.939,
    fromNode: "MUM-N000000",
    toNode: "MUM-N034315",
    ...over,
  };
}

function node(over: Partial<DrainNode> = {}): DrainNode {
  return {
    id: "MUM-N000000",
    lon: 72.82,
    lat: 19.0,
    kind: "inlet",
    zGroundM: 9.82,
    zInvertM: 8.32,
    isOutfall: false,
    tidal: false,
    ...over,
  };
}

/** Accessors the way deck calls them, so a prop rename fails here. */
interface LayerLike {
  id: string;
  props: Record<string, unknown>;
}

function layerById(layers: unknown[], id: string): LayerLike {
  const found = (layers as LayerLike[]).find((layer) => layer.id === id);
  if (!found) throw new Error(`No layer ${id} among ${(layers as LayerLike[]).map((l) => l.id)}`);
  return found;
}

function ready(result: ReturnType<typeof drains3dLayers>): Drains3dReady {
  if (result.kind !== "ready") throw new Error(`Expected a ready X-ray, got ${result.kind}`);
  return result;
}

describe("pipeProfile", () => {
  it("interpolates z by distance along the line, not by vertex index", () => {
    // Four vertices on one parallel, so the longitude scaling is a common factor that cancels
    // out of every fraction: legs of 1, 3 and 1 degree-units, total 5.
    const path: [number, number][] = [
      [0, 19],
      [1, 19],
      [4, 19],
      [5, 19],
    ];
    const z = pipeProfile(path, 10, 0).map((p) => p[2]);
    // Falling 10 m over 5 units: 0, 1/5, 4/5, 1 of the way down.
    expect(z[0]).toBeCloseTo(10, 9);
    expect(z[1]).toBeCloseTo(8, 9);
    expect(z[2]).toBeCloseTo(2, 9);
    expect(z[3]).toBeCloseTo(0, 9);
    // The index-based answer would have put the middle vertices at 6.667 and 3.333.
    expect(z[1]).not.toBeCloseTo(10 - 10 / 3, 3);
  });

  it("keeps the vertices where they were and only adds a third component", () => {
    const path: [number, number][] = [
      [72.82, 19.0],
      [72.821, 19.001],
    ];
    const lifted = pipeProfile(path, 5, 4);
    expect(lifted.map((p) => [p[0], p[1]])).toEqual(path);
    expect(lifted[0][2]).toBe(5);
    expect(lifted[1][2]).toBe(4);
  });

  it("scales a longitude step by cos(latitude), so a corner is placed on the right leg", () => {
    // An L: one thousandth of a degree east, then one thousandth north, at 19 degrees N.
    const path: [number, number][] = [
      [0, 19],
      [0.001, 19],
      [0.001, 19.001],
    ];
    const cos19 = Math.cos((19 * Math.PI) / 180);
    const east = 0.001 * cos19;
    const north = 0.001;
    const expected = 10 - 10 * (east / (east + north));
    expect(pipeProfile(path, 10, 0)[1][2]).toBeCloseTo(expected, 9);
    // And it is not the unscaled half-way answer.
    expect(pipeProfile(path, 10, 0)[1][2]).not.toBeCloseTo(5, 4);
  });

  it("gives a pipe with no extent its upstream invert rather than dividing by zero", () => {
    const same: [number, number][] = [
      [72.82, 19.0],
      [72.82, 19.0],
    ];
    expect(pipeProfile(same, 8.32, 7.9).map((p) => p[2])).toEqual([8.32, 8.32]);
    expect(pipeProfile([], 1, 2)).toEqual([]);
  });
});

describe("drawnInvert", () => {
  it("leaves the invert where it is at the default exaggeration of 1", () => {
    expect(drawnInvert(8.32, 9.82, 1, 0)).toEqual({ z: 8.32, usedGround: false });
  });

  it("stretches the cover below the street, never the elevation", () => {
    // 1.5 m of cover at 3x is 4.5 m below a street at 9.82 m.
    expect(drawnInvert(8.32, 9.82, 3, 0).z).toBeCloseTo(5.32, 9);
    // Multiplying the elevation instead would have put this pipe at 24.96 m, in the air.
    expect(drawnInvert(8.32, 9.82, 3, 0).z).toBeLessThan(8.32);
  });

  it("draws the real depth and says so when there is no street level above the pipe", () => {
    expect(drawnInvert(8.32, undefined, 3, 0)).toEqual({ z: 8.32, usedGround: false });
  });

  it("adds the datum offset to every z, exaggerated or not", () => {
    expect(drawnInvert(8.32, 9.82, 1, -64).z).toBeCloseTo(-55.68, 9);
    expect(drawnInvert(8.32, 9.82, 3, -64).z).toBeCloseTo(-58.68, 9);
  });
});

describe("selection", () => {
  it("drops an edge the served layer gave no invert for", () => {
    const withZ = edge();
    const withoutZ = edge({ id: "MUM-E000001", zUpM: undefined, zDnM: undefined });
    expect(selectPipes([withZ, withoutZ], null).map((e) => e.id)).toEqual(["MUM-E000000"]);
  });

  it("keeps only the pipes whose extent meets the view", () => {
    const inside = edge();
    const outside = edge({
      id: "MUM-E000009",
      path: [
        [73.5, 19.0],
        [73.51, 19.0],
      ],
    });
    expect(selectPipes([inside, outside], ALL).map((e) => e.id)).toEqual(["MUM-E000000"]);
    expect(selectPipes([inside, outside], null)).toHaveLength(2);
  });

  it("holds the shafts back below the zoom gate and while the canvas is unmeasured", () => {
    const nodes = [node()];
    expect(selectShafts(nodes, SHAFT_MIN_ZOOM - 0.1, ALL)).toHaveLength(0);
    expect(selectShafts(nodes, SHAFT_MIN_ZOOM, null)).toHaveLength(0);
    expect(selectShafts(nodes, SHAFT_MIN_ZOOM, ALL)).toHaveLength(1);
  });

  it("gives no shaft to an outfall or to a node with no shaft to draw", () => {
    const nodes = [
      node(),
      node({ id: "OUT", kind: "outfall", isOutfall: true }),
      node({ id: "FLAT", zGroundM: 8.32 }),
    ];
    expect(selectShafts(nodes, 17, ALL).map((n) => n.id)).toEqual(["MUM-N000000"]);
  });

  it("culls shafts to the view", () => {
    const nodes = [node(), node({ id: "FAR", lon: 73.5 })];
    expect(selectShafts(nodes, 17, ALL).map((n) => n.id)).toEqual(["MUM-N000000"]);
  });

  it("takes every outfall at every zoom", () => {
    const nodes = [
      node(),
      node({ id: "OUT", kind: "outfall", isOutfall: true }),
      node({ id: "TIDE", kind: "outfall", isOutfall: true, tidal: true, lon: 73.5 }),
    ];
    expect(selectOutfalls(nodes).map((n) => n.id)).toEqual(["OUT", "TIDE"]);
  });

  it("calls a pipe adverse when its downstream invert is the higher one", () => {
    expect(isAdverse(edge())).toBe(false);
    expect(isAdverse(edge({ zUpM: 5.734, zDnM: 11.845 }))).toBe(true);
    expect(isAdverse(edge({ zUpM: undefined }))).toBe(false);
  });
});

describe("drains3dLayers", () => {
  const nodes = [
    node(),
    node({ id: "MUM-N034315", lon: 72.821, lat: 19.0, zGroundM: 9.439, zInvertM: 7.939 }),
    node({ id: "OUT", kind: "outfall", isOutfall: true, lon: 72.83, lat: 19.01 }),
    node({ id: "TIDE", kind: "outfall", isOutfall: true, tidal: true, lon: 72.84, lat: 19.02 }),
  ];
  const base = { city: "mumbai", drains: [edge()], nodes, show: true, zoom: 17, bounds: ALL };

  it("is off when the operator has not asked for it, and loading before the network arrives", () => {
    expect(drains3dLayers({ ...base, show: false })).toEqual({ kind: "off" });
    expect(drains3dLayers({ ...base, drains: [] })).toEqual({ kind: "loading" });
  });

  it("refuses to draw, and names the command, when the layer carries no elevations", () => {
    const result = drains3dLayers({
      ...base,
      drains: [edge({ zUpM: undefined, zDnM: undefined })],
    });
    expect(result.kind).toBe("unavailable");
    if (result.kind !== "unavailable") throw new Error("unreachable");
    expect(result.message).toContain("no invert elevations");
    expect(result.message).toContain("make city CITY=mumbai");
    // No layers at all, so nothing can be drawn at a defaulted zero.
    expect("layers" in result).toBe(false);
  });

  it("names the city it was asked about in that message", () => {
    const result = drains3dLayers({
      ...base,
      city: "chennai",
      drains: [edge({ zUpM: undefined, zDnM: undefined })],
    });
    if (result.kind !== "unavailable") throw new Error("unreachable");
    expect(result.message).toContain("make city CITY=chennai");
  });

  it("draws pipes, shafts and outfalls, and counts what it drew", () => {
    const result = ready(drains3dLayers(base));
    expect(result.pipesDrawn).toBe(1);
    expect(result.shaftsDrawn).toBe(2);
    expect(result.outfallsDrawn).toBe(2);
    expect(result.tidalOutfallsDrawn).toBe(1);
    expect(result.shaftsGated).toBe(false);
    expect((result.layers as LayerLike[]).map((l) => l.id)).toEqual([
      "drains-3d-pipes",
      "drains-3d-shafts",
      "drains-3d-outfalls",
    ]);
  });

  it("counts the pipes it could not place separately from the ones it drew", () => {
    const result = ready(
      drains3dLayers({ ...base, drains: [edge(), edge({ id: "B", zUpM: undefined })] }),
    );
    expect(result.pipesDrawn).toBe(1);
    expect(result.pipesWithoutElevation).toBe(1);
  });

  it("takes every pipe's colour from drainColour and never from a literal", () => {
    const betas = [0, 0.2, 0.3, 0.6, 0.9];
    const result = ready(
      drains3dLayers({
        ...base,
        drains: betas.map((beta, i) => edge({ id: `E${i}`, beta })),
      }),
    );
    const layer = layerById(result.layers, "drains-3d-pipes");
    const getColor = layer.props.getColor as (d: { beta: number }) => number[];
    for (const beta of betas) expect(getColor({ beta })).toEqual(drainColour(beta));
  });

  it("draws a pipe at its diameter in metres, with a pixel floor", () => {
    const layer = layerById(ready(drains3dLayers(base)).layers, "drains-3d-pipes");
    expect(layer.props.widthUnits).toBe("meters");
    expect((layer.props.getWidth as (d: { diameter: number }) => number)({ diameter: 0.9 })).toBe(
      0.9,
    );
    expect(layer.props.widthMinPixels).toBe(PIPE_MIN_PIXELS);
  });

  it("keeps the inferred dash `layers/drains.ts` uses, from that module", () => {
    const layer = layerById(ready(drains3dLayers(base)).layers, "drains-3d-pipes");
    expect(layer.props.extensions).toEqual([DASHED]);
    expect(layer.props.getDashArray).toBe(DRAIN_DASH);
  });

  it("lifts the pipe onto its own invert profile", () => {
    const layer = layerById(ready(drains3dLayers(base)).layers, "drains-3d-pipes");
    const data = layer.props.data as { path: number[][] }[];
    expect(data[0].path[0][2]).toBeCloseTo(8.32, 9);
    expect(data[0].path[1][2]).toBeCloseTo(7.939, 9);
  });

  it("runs the shaft from the invert up to the street at the node's own position", () => {
    const layer = layerById(ready(drains3dLayers(base)).layers, "drains-3d-shafts");
    const data = layer.props.data as { id: string; path: number[][] }[];
    const shaft = data.find((d) => d.id === "MUM-N000000");
    expect(shaft?.path[0]).toEqual([72.82, 19.0, 8.32]);
    expect(shaft?.path[1]).toEqual([72.82, 19.0, 9.82]);
    expect(layer.props.getColor).toBe(SHAFT_COLOUR);
    expect(layer.props.widthUnits).toBe("meters");
  });

  it("marks a tide-locked outfall apart from an ordinary one", () => {
    const layer = layerById(ready(drains3dLayers(base)).layers, "drains-3d-outfalls");
    const getLineColor = layer.props.getLineColor as (d: { tidal: boolean }) => number[];
    const getRadius = layer.props.getRadius as (d: { tidal: boolean }) => number;
    expect(getLineColor({ tidal: true })).toEqual(SURCHARGE_LINE);
    expect(TIDAL_OUTFALL_COLOUR).toEqual(SURCHARGE_LINE);
    expect(getLineColor({ tidal: false })).toEqual(SHAFT_COLOUR);
    expect(getRadius({ tidal: true })).toBeGreaterThan(getRadius({ tidal: false }));
    expect(layer.props.radiusUnits).toBe("meters");
  });

  it("draws no shaft layer at all below the gate, and says the shafts are gated", () => {
    const result = ready(drains3dLayers({ ...base, zoom: 13 }));
    expect(result.shaftsDrawn).toBe(0);
    expect(result.shaftsGated).toBe(true);
    expect((result.layers as LayerLike[]).map((l) => l.id)).toEqual([
      "drains-3d-pipes",
      "drains-3d-outfalls",
    ]);
  });

  it("stretches the cover when asked, using the nodes' street levels", () => {
    const result = ready(drains3dLayers({ ...base, depthExaggeration: 4 }));
    const data = layerById(result.layers, "drains-3d-pipes").props.data as { path: number[][] }[];
    // 1.5 m of cover at each end, stretched to 6 m below streets at 9.82 m and 9.439 m.
    expect(data[0].path[0][2]).toBeCloseTo(3.82, 9);
    expect(data[0].path[1][2]).toBeCloseTo(3.439, 9);
    expect(result.exaggerationApplied).toBe(true);
    expect(result.pipesWithoutGround).toBe(0);
  });

  it("counts a pipe whose street level it never got, rather than half-stretching the network", () => {
    const result = ready(
      drains3dLayers({ ...base, nodes: [], depthExaggeration: 4, zoom: 17 }),
    );
    const data = layerById(result.layers, "drains-3d-pipes").props.data as { path: number[][] }[];
    expect(data[0].path[0][2]).toBeCloseTo(8.32, 9);
    expect(result.pipesWithoutGround).toBe(1);
    expect(result.exaggerationApplied).toBe(false);
  });

  it("shifts every z by the datum offset", () => {
    const result = ready(drains3dLayers({ ...base, datumOffsetM: -64 }));
    const pipe = layerById(result.layers, "drains-3d-pipes").props.data as { path: number[][] }[];
    const shaft = layerById(result.layers, "drains-3d-shafts").props.data as {
      id: string;
      path: number[][];
    }[];
    const outfall = layerById(result.layers, "drains-3d-outfalls").props.data as {
      position: number[];
    }[];
    expect(pipe[0].path[0][2]).toBeCloseTo(8.32 - 64, 9);
    expect(shaft.find((s) => s.id === "MUM-N000000")?.path[1][2]).toBeCloseTo(9.82 - 64, 9);
    expect(outfall[0].position[2]).toBeCloseTo(8.32 - 64, 9);
  });

  it("counts the uphill pipes among the ones it drew", () => {
    const result = ready(
      drains3dLayers({
        ...base,
        drains: [edge(), edge({ id: "UP", zUpM: 5.734, zDnM: 11.845 })],
      }),
    );
    expect(result.pipesDrawn).toBe(2);
    expect(result.adverseDrawn).toBe(1);
  });
});

describe("viewKey", () => {
  it("is the same for a pan below its resolution and different above it", () => {
    const a: Bbox = [
      [72.8, 18.99],
      [72.9, 19.13],
    ];
    const b: Bbox = [
      [72.80004, 18.99004],
      [72.90004, 19.13004],
    ];
    const c: Bbox = [
      [72.81, 18.99],
      [72.91, 19.13],
    ];
    expect(viewKey(15.1, a)).toBe(viewKey(15.1, b));
    expect(viewKey(15.1, a)).not.toBe(viewKey(15.1, c));
    expect(viewKey(15.1, a)).not.toBe(viewKey(16, a));
    expect(viewKey(15, null)).toBe("15:none");
  });
});

describe("copy", () => {
  it("says what the exaggeration is doing, or that there is none", () => {
    expect(exaggerationLabel(1)).toBe("Real depth");
    expect(exaggerationLabel(4)).toBe("Depth below the street stretched 4x");
  });

  it("names every state rather than leaving a blank panel", () => {
    expect(drainXraySummary({ kind: "off" })).toBe("The drain X-ray is off.");
    expect(drainXraySummary({ kind: "loading" })).toContain("Loading");
    expect(drainXraySummary({ kind: "unavailable", message: "No inverts." })).toBe("No inverts.");
  });

  it("reports the pipes it could not place, the uphill ones and the gated shafts", () => {
    const summary = drainXraySummary({
      kind: "ready",
      layers: [],
      pipesDrawn: 21296,
      pipesWithoutElevation: 12,
      adverseDrawn: 8000,
      shaftsDrawn: 0,
      outfallsDrawn: 127,
      tidalOutfallsDrawn: 3,
      shaftsGated: true,
      exaggerationApplied: false,
      pipesWithoutGround: 0,
    });
    expect(summary).toContain("21,296 inferred pipes drawn");
    expect(summary).toContain("12 more carry no invert elevation");
    expect(summary).toContain("8,000 of them run uphill");
    expect(summary).toContain(`Manhole shafts appear from zoom ${SHAFT_MIN_ZOOM}`);
    expect(summary).toContain("3 of 127 outfalls are tide-locked");
    expect(summary).not.toContain("undefined");
  });

  it("counts the shafts in view once they are drawn", () => {
    const summary = drainXraySummary({
      kind: "ready",
      layers: [],
      pipesDrawn: 340,
      pipesWithoutElevation: 0,
      adverseDrawn: 0,
      shaftsDrawn: 412,
      outfallsDrawn: 0,
      tidalOutfallsDrawn: 0,
      shaftsGated: false,
      exaggerationApplied: false,
      pipesWithoutGround: 0,
    });
    expect(summary).toBe(
      "340 inferred pipes drawn at their invert depth under the street. 412 manhole shafts in view.",
    );
  });
});
