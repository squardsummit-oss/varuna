/**
 * The city's own GIS, for the map's context layers (`GET /v1/city/{city}/layers/{name}`).
 *
 * These are the layers that make the console look like a map of Mumbai rather than a chart of
 * some lines: 39,259 building footprints and the 49,770-edge inferred drain graph. Both are
 * large — the drains are 18 MB of GeoJSON — so neither is on the path to first paint. The map
 * draws the run as soon as it has one and these arrive behind it, buildings first because they
 * are what the eye reads as "a city", drains only when the operator turns them on.
 */

import { apiUrl } from "@/lib/api/client";

/** A building footprint's outer ring, in lon/lat. */
export type BuildingPolygon = [number, number][];

export interface DrainPath {
  /** Edge id, so a run's learned posterior can be joined onto the full network. */
  id: string;
  path: [number, number][];
  /** Prior blockage 0-1 from the city pipeline; Pulse replaces it with a posterior in Phase 7. */
  beta: number;
  diameter: number;
  /**
   * Invert elevation in metres at the from-node and the to-node, in the DEM's vertical frame.
   * `undefined` when the served layer predates the export change that added them, which the
   * X-ray reports as a named state rather than drawing the network at zero.
   */
  zUpM?: number;
  zDnM?: number;
  /**
   * The design slope the city pipeline stored on the edge.
   *
   * **Carried, never drawn from.** It and the two inverts disagree on 48,055 of Mumbai's 49,770
   * edges at a 1e-6 tolerance (measured 2026-09-23 over `city/mumbai/drain_edges.parquet`;
   * ADR-0048 counts 26,622 by its own stricter-on-sign criterion and 18,994 edges that run
   * uphill). Inverts are what the 1D solver reads and what the pipe physically sits at, so the
   * X-ray interpolates between `zUpM` and `zDnM` and this field is here only for a pipe popover
   * to quote alongside them.
   */
  slope?: number;
  /** The node ids at each end, for joining the nodes layer's ground elevations. */
  fromNode?: string;
  toNode?: string;
}

/**
 * One node of the inferred drain graph, as `GET /v1/city/{city}/layers/drain_nodes` serves it.
 *
 * Structurally the same shape as `components/map/layers/types.ts`'s `DrainNode`, mirrored rather
 * than imported for the same reason `DrainPath` is: the layer types are pure drawing types with
 * no API imports, and this module is the API client. Keep the two in step.
 */
export interface DrainNode {
  id: string;
  lon: number;
  lat: number;
  /** inlet, depression, trunk, outfall or hotspot in Mumbai's export. */
  kind: string;
  zGroundM: number;
  zInvertM: number;
  isOutfall: boolean;
  tidal: boolean;
}

interface Feature {
  properties?: Record<string, unknown> | null;
  geometry?: { type?: string; coordinates?: unknown } | null;
}

async function layer(city: string, name: string, signal?: AbortSignal): Promise<Feature[]> {
  const response = await fetch(apiUrl(`/v1/city/${city}/layers/${name}`), { signal });
  if (!response.ok) throw new Error(`Layer ${name}: HTTP ${response.status}`);
  const body = (await response.json()) as { features?: Feature[] };
  return body.features ?? [];
}

/**
 * Building outlines, flattened to outer rings.
 *
 * Only the outer ring of each footprint: courtyards are a metre or two of detail at a zoom where
 * the whole building is a few pixels, and carrying them would double the geometry for something
 * nobody can see.
 */
export async function loadBuildings(
  city: string,
  signal?: AbortSignal,
): Promise<BuildingPolygon[]> {
  const features = await layer(city, "buildings", signal);
  const out: BuildingPolygon[] = [];
  for (const feature of features) {
    const geometry = feature.geometry;
    if (!geometry) continue;
    if (geometry.type === "Polygon") {
      const rings = geometry.coordinates as BuildingPolygon[];
      if (rings?.[0]?.length) out.push(rings[0]);
    } else if (geometry.type === "MultiPolygon") {
      for (const rings of (geometry.coordinates as BuildingPolygon[][]) ?? []) {
        if (rings?.[0]?.length) out.push(rings[0]);
      }
    }
  }
  return out;
}

/** A number the served layer may not carry at all: `undefined` rather than a defaulted 0. */
function optionalNumber(value: unknown): number | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

/**
 * The inferred drain graph as drawable paths, with the blockage prior each edge carries and, when
 * the served layer carries them, the invert elevations the 3D X-ray places the pipe at.
 *
 * The elevations are read with {@link optionalNumber} rather than `Number(x ?? 0)` on purpose: a
 * missing invert defaulted to 0 would put that pipe at mean sea level, and Mumbai's inverts have
 * a median of 9.58 m and reach 81.0 m, so the default would bury most of the network by ten
 * metres and some of it by eighty. Absent has to stay absent so the X-ray can say the layer does
 * not carry them, which is the one answer that cannot mislead.
 */
export async function loadDrains(city: string, signal?: AbortSignal): Promise<DrainPath[]> {
  const features = await layer(city, "drains", signal);
  const out: DrainPath[] = [];
  for (const feature of features) {
    if (feature.geometry?.type !== "LineString") continue;
    const props = feature.properties ?? {};
    out.push({
      id: String(props.edge_id ?? props.id ?? ""),
      path: feature.geometry.coordinates as [number, number][],
      beta: Number(props.beta_mean ?? props.beta ?? 0),
      diameter: Number(props.diameter_m ?? props.height_m ?? 0.6),
      zUpM: optionalNumber(props.z_invert_up_m),
      zDnM: optionalNumber(props.z_invert_dn_m),
      slope: optionalNumber(props.slope),
      fromNode: optionalString(props.from_node),
      toNode: optionalString(props.to_node),
    });
  }
  return out;
}

/**
 * The drain graph's nodes: manhole shafts, inlets, depression bottoms, trunk junctions, outfalls.
 *
 * 49,897 points for Mumbai, each about 180 bytes of GeoJSON, so this is a several-megabyte fetch
 * like the edges and belongs behind the same "only when the operator asks for it" gate. A node
 * whose ground or invert elevation is missing or not a number is dropped here rather than drawn
 * at an invented depth: the shaft it would draw is the one thing on this map that claims to know
 * how far under the street the pipe is.
 */
export async function loadDrainNodes(city: string, signal?: AbortSignal): Promise<DrainNode[]> {
  const features = await layer(city, "drain_nodes", signal);
  const out: DrainNode[] = [];
  for (const feature of features) {
    const geometry = feature.geometry;
    if (geometry?.type !== "Point") continue;
    const props = feature.properties ?? {};
    const zGroundM = optionalNumber(props.z_ground_m);
    const zInvertM = optionalNumber(props.z_invert_m);
    if (zGroundM === undefined || zInvertM === undefined) continue;
    const coords = geometry.coordinates as number[];
    const lon = optionalNumber(coords?.[0]);
    const lat = optionalNumber(coords?.[1]);
    if (lon === undefined || lat === undefined) continue;
    out.push({
      id: String(props.node_id ?? props.id ?? ""),
      lon,
      lat,
      kind: String(props.kind ?? ""),
      zGroundM,
      zInvertM,
      isOutfall: props.is_outfall === true || props.kind === "outfall",
      tidal: props.tidal === true,
    });
  }
  return out;
}

/** A named facility the map labels: hospitals, fire stations and railway stations. */
export interface FacilityLabel {
  id: string;
  text: string;
  lon: number;
  lat: number;
  kind: "hospital" | "fire_station" | "station";
}

/** Which asset kinds get a name on the map, and how many of each.
 *
 * Mumbai's asset layer has 354 hospitals and 242 shelters, and naming all of them would bury the
 * map in text that says nothing about water. Hospitals and fire stations are what a route and a
 * reachability clock are *about*; stations are how a commuter locates themselves. Shelters,
 * depots and pumping stations stay unnamed until a screen asks for them. */
const LABELLED_KINDS: Record<string, FacilityLabel["kind"]> = {
  hospital: "hospital",
  fire_station: "fire_station",
  station: "station",
};

/** Named hospitals, fire stations and stations, for the map's label layer. */
export async function loadFacilityLabels(
  city: string,
  signal?: AbortSignal,
): Promise<FacilityLabel[]> {
  const features = await layer(city, "assets", signal);
  const out: FacilityLabel[] = [];
  for (const feature of features) {
    const props = feature.properties ?? {};
    const kind = LABELLED_KINDS[String(props.kind ?? "")];
    const geometry = feature.geometry;
    if (!kind || geometry?.type !== "Point") continue;
    const name = typeof props.name === "string" ? props.name.trim() : "";
    // An unnamed hospital is a dot with nothing to say; the marker layer already draws the dot.
    if (!name) continue;
    const coords = geometry.coordinates as number[];
    out.push({
      id: String(props.asset_id ?? name),
      text: name,
      lon: Number(coords[0]),
      lat: Number(coords[1]),
      kind,
    });
  }
  return out;
}
