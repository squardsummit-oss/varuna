/**
 * Contract tests for the city-layer client's drain graph (chunk DRAINS).
 *
 * The fixtures are the shape `services/city/varuna_city/export.py` writes into
 * `city/<city>/map/*.geojson` and `GET /v1/city/{city}/layers/{name}` serves verbatim, property
 * for property, so a rename in that allow-list fails here rather than in a 3D view where a pipe
 * would silently move to sea level.
 *
 * Two fixtures matter most: one drains feature **with** the invert elevations, as the export
 * writes them once `z_invert_up_m` and `z_invert_dn_m` are in `MAP_KEEP_COLUMNS`, and one
 * **without**, as a deployed API serving a city exported before that change still answers. The
 * second must come back with `undefined` inverts and not zeroes.
 *
 * This file sits in `__tests__/` rather than beside `city-layers.ts` the way the other API tests
 * do, because that is the path this chunk was given to own; see `.wf/DRAINS-requests.md`.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { loadDrainNodes, loadDrains } from "@/lib/api/city-layers";

/** One drain edge as `city/mumbai/map/drains.geojson` carries it, inverts included. */
function drainFeature(
  properties: Record<string, unknown> = {},
  geometry: unknown = {
    type: "LineString",
    coordinates: [
      [72.820186, 18.99601],
      [72.81985, 18.996036],
    ],
  },
) {
  return {
    type: "Feature",
    properties: {
      edge_id: "MUM-E000000",
      from_node: "MUM-N000000",
      to_node: "MUM-N034315",
      length_m: 35.518,
      slope: 0.010735,
      z_invert_up_m: 8.32,
      z_invert_dn_m: 7.939,
      shape: "circular",
      diameter_m: 0.6,
      width_m: null,
      height_m: null,
      is_trunk: false,
      beta_mean: 0.2,
      beta_sd: 0.1512,
      confidence: "inferred",
      ...properties,
    },
    geometry,
  };
}

/** One node as `city/mumbai/map/drain_nodes.geojson` carries it. */
function nodeFeature(
  properties: Record<string, unknown> = {},
  geometry: unknown = { type: "Point", coordinates: [72.820186, 18.99601] },
) {
  return {
    type: "Feature",
    properties: {
      node_id: "MUM-N000000",
      kind: "inlet",
      is_outfall: false,
      tidal: false,
      flap_gate: false,
      z_ground_m: 9.82,
      z_invert_m: 8.32,
      kappa_mean: 0.25,
      segment_id: "S0-000",
      surface_unit_id: "U-008383",
      downstream_node: "MUM-N034315",
      confidence: "inferred",
      ...properties,
    },
    geometry,
  };
}

/** Serves one collection and records the paths asked for. */
function serve(features: unknown[], asked: string[] = []): string[] {
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    asked.push(String(input));
    return new Response(JSON.stringify({ type: "FeatureCollection", features }), {
      status: 200,
      headers: { "content-type": "application/geo+json" },
    });
  });
  return asked;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("loadDrains", () => {
  it("carries the invert elevations, the slope and both node ids through", async () => {
    serve([drainFeature()]);
    const [edge] = await loadDrains("mumbai");
    expect(edge.id).toBe("MUM-E000000");
    expect(edge.zUpM).toBe(8.32);
    expect(edge.zDnM).toBe(7.939);
    expect(edge.slope).toBe(0.010735);
    expect(edge.fromNode).toBe("MUM-N000000");
    expect(edge.toNode).toBe("MUM-N034315");
    expect(edge.beta).toBe(0.2);
    expect(edge.diameter).toBe(0.6);
    expect(edge.path).toHaveLength(2);
  });

  it("leaves a missing invert undefined rather than defaulting it to sea level", async () => {
    // A city exported before the elevations joined MAP_KEEP_COLUMNS: the properties are simply
    // absent. Zero here would bury this pipe eight metres below where it is.
    serve([
      drainFeature({
        z_invert_up_m: undefined,
        z_invert_dn_m: undefined,
        slope: undefined,
        from_node: undefined,
        to_node: undefined,
      }),
    ]);
    const [edge] = await loadDrains("mumbai");
    expect(edge.zUpM).toBeUndefined();
    expect(edge.zDnM).toBeUndefined();
    expect(edge.slope).toBeUndefined();
    expect(edge.fromNode).toBeUndefined();
    expect(edge.toNode).toBeUndefined();
    // Everything the 2D layer already drew is unchanged.
    expect(edge.beta).toBe(0.2);
    expect(edge.diameter).toBe(0.6);
  });

  it("treats an explicit null the same as an absent property", async () => {
    serve([drainFeature({ z_invert_up_m: null, z_invert_dn_m: null })]);
    const [edge] = await loadDrains("mumbai");
    expect(edge.zUpM).toBeUndefined();
    expect(edge.zDnM).toBeUndefined();
  });

  it("keeps a negative invert, which is where the tide-locked outfalls are", async () => {
    serve([drainFeature({ z_invert_up_m: -2.655, z_invert_dn_m: -10.435 })]);
    const [edge] = await loadDrains("mumbai");
    expect(edge.zUpM).toBe(-2.655);
    expect(edge.zDnM).toBe(-10.435);
  });

  it("asks for the drains layer of the city it was given", async () => {
    const asked = serve([drainFeature()]);
    await loadDrains("chennai");
    expect(asked[0]).toContain("/v1/city/chennai/layers/drains");
  });

  it("skips a feature that is not a line", async () => {
    serve([drainFeature({}, { type: "Point", coordinates: [72.82, 19.0] })]);
    expect(await loadDrains("mumbai")).toEqual([]);
  });
});

describe("loadDrainNodes", () => {
  it("reads the ground and invert elevations and the outfall flags", async () => {
    serve([nodeFeature()]);
    const [node] = await loadDrainNodes("mumbai");
    expect(node).toEqual({
      id: "MUM-N000000",
      lon: 72.820186,
      lat: 18.99601,
      kind: "inlet",
      zGroundM: 9.82,
      zInvertM: 8.32,
      isOutfall: false,
      tidal: false,
    });
  });

  it("marks a tide-locked outfall, which is where the reversed flow enters", async () => {
    serve([
      nodeFeature({ node_id: "MUM-N049999", kind: "outfall", is_outfall: true, tidal: true }),
    ]);
    const [node] = await loadDrainNodes("mumbai");
    expect(node.isOutfall).toBe(true);
    expect(node.tidal).toBe(true);
  });

  it("treats a node whose kind is outfall as one even if the flag is missing", async () => {
    serve([nodeFeature({ kind: "outfall", is_outfall: undefined })]);
    expect((await loadDrainNodes("mumbai"))[0].isOutfall).toBe(true);
  });

  it("drops a node with no elevation rather than inventing a depth for its shaft", async () => {
    serve([
      nodeFeature({ node_id: "NO-GROUND", z_ground_m: null }),
      nodeFeature({ node_id: "NO-INVERT", z_invert_m: undefined }),
      nodeFeature({ node_id: "KEPT" }),
    ]);
    expect((await loadDrainNodes("mumbai")).map((n) => n.id)).toEqual(["KEPT"]);
  });

  it("drops a feature that is not a point", async () => {
    serve([
      nodeFeature({}, { type: "LineString", coordinates: [[72.82, 19.0]] }),
      nodeFeature({ node_id: "KEPT" }),
    ]);
    expect((await loadDrainNodes("mumbai")).map((n) => n.id)).toEqual(["KEPT"]);
  });

  it("asks for the drain_nodes layer", async () => {
    const asked = serve([nodeFeature()]);
    await loadDrainNodes("mumbai");
    expect(asked[0]).toContain("/v1/city/mumbai/layers/drain_nodes");
  });

  it("reports a layer the city has not built rather than returning an empty network", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response(JSON.stringify({ error: { code: "layer_not_built" } }), { status: 404 }),
    );
    await expect(loadDrainNodes("mumbai")).rejects.toThrow("HTTP 404");
  });
});
