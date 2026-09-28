/**
 * The drain X-ray: the inferred pipe network drawn underground, at the depth it actually sits,
 * with its manhole shafts rising to the street above it (motion M28, SPEC.md 7.3).
 *
 * This is the "walk the drains through the streets" view. `layers/drains.ts` draws the same
 * network flat, as lines on a plan; this draws it as objects in the city's own 3D space, so with
 * the photorealistic surface faded down (M28) the reader sees a 600 mm pipe running under a road
 * they recognise, 1.5 m below the tarmac, with a shaft up to a manhole cover they can see.
 *
 * **Where the depth comes from.** `city/<city>/drain_edges.parquet` gives every edge the invert
 * elevation at each of its ends (`z_invert_up_m`, `z_invert_dn_m`) and every node its street level
 * and its invert (`z_ground_m`, `z_invert_m`). The edge's own polyline is lifted by interpolating
 * between the two inverts along the line's **length**, not its vertex index: an inferred pipe
 * follows a road centreline whose vertices sit where the road bends, which is nowhere near evenly
 * spaced.
 *
 * The stored `slope` column is deliberately not used. It and the two inverts imply different
 * gradients on 48,055 of Mumbai's 49,770 edges at a 1e-6 tolerance (measured 2026-09-23 over
 * `city/mumbai/drain_edges.parquet`), and 18,994 edges fall the wrong way entirely - the graph is
 * not gravity-consistent and ADR-0048 says so. The inverts are what `drain1d` solves on, so they
 * are what the reader should be shown. {@link drainXraySummary} says out loud how many of the
 * pipes on screen run uphill, because in 3D that is visible and an unexplained uphill sewer reads
 * as a broken renderer rather than as a finding.
 *
 * **What frame the z is in.** Metres in the DEM's own vertical frame, which for a city built from
 * Copernicus GLO-30 is orthometric (EGM2008), and which ADR-0055 already notes is not reconciled
 * with local mean sea level. Google's photorealistic 3D tiles put their ground at WGS84
 * **ellipsoidal** height, and the two differ over western India by tens of metres - so with those
 * tiles as the ground, a network drawn at its orthometric z is displaced from the street it runs
 * under by the whole geoid separation. {@link Drains3dOptions.datumOffsetM} is that shift.
 *
 * It defaults to 0 and **this chunk measured neither its size nor its sign**: pyproj on this
 * machine has no EGM2008 grid and `EPSG:9518 -> EPSG:4979` returned an unshifted 0.0, which is
 * not a measurement. Until someone measures it, the X-ray over Google's tiles is right relative
 * to itself and unverified relative to the ground, and a guessed offset would be worse than the
 * visible float: a float is obviously wrong, and a wrong-by-15-m offset looks right.
 *
 * **Why these layers carry no `TerrainExtension`.** That extension drapes a layer onto the terrain
 * surface, which is exactly what must not happen here: a draped pipe is a line painted on the
 * road. These layers are drawn in world space at their own z, under the surface.
 *
 * **What it costs.** One call of {@link drains3dLayers} over Mumbai's whole graph - 49,770 edges
 * and 49,897 nodes read from `city/mumbai/map/`, inverts joined from the end nodes - measured on
 * 2026-09-23 on this laptop with 346 processes running (12 node, 8 python). Each figure is the
 * spread of the medians of nine calls over five separate runs, so it carries the machine's own
 * variance rather than the best run:
 *
 * - citywide at zoom 12, all 49,658 pipes in view, no shafts: **27.3-59.8 ms**, worst single
 *   call 106.4 ms.
 * - a ward at zoom 15, 4,991 pipes and 4,925 shafts: **12.6-18.1 ms**.
 * - a street at zoom 17, 196 pipes and 179 shafts: **10.1-14.8 ms**; at 4x cover, 12.3-17.2 ms.
 *
 * This is a rebuild, not a per-frame cost - {@link viewKey} keeps a pan that changes nothing on
 * screen from paying it - but **the citywide rebuild misses a 16 ms frame and the street view is
 * at its edge**. Two things would fix that and neither is built: handing deck binary path
 * attributes (one `Float64Array` plus `startIndices`) instead of an array of arrays, which is
 * where the citywide time goes lifting 49,658 polylines; and a spatial index, so the cull stops
 * being a scan of all 49,770 edges and all 49,897 nodes - that scan is why a street view drawing
 * 196 pipes still costs 10 ms. None of this has been measured with a GPU attached: jsdom has no
 * WebGL, so deck's own attribute upload and draw are **not** in any number above.
 */

import { ScatterplotLayer, PathLayer } from "@deck.gl/layers";
import type { PathStyleExtensionProps } from "@deck.gl/extensions";

import type { Bbox } from "../basemap";
import { DASHED, DRAIN_DASH } from "./drains";
import { drainColour, SURCHARGE_LINE, type Rgba } from "./palette";
import type { DrainNode, DrainPath } from "./types";

// ---- Colour ------------------------------------------------------------------------------------

/**
 * `--line-strong` #33436A: the manhole shafts and the ordinary outfalls.
 *
 * Structure, not water and not blockage. The one ramp that must stay readable down here is the
 * magenta blockage ramp on the pipes, so everything that is merely *built* is drawn in the border
 * token and gets out of its way. It is not `--depth-dry`, close as that is in value: section 6.2
 * forbids the depth ramp for anything that is not water depth, and a shaft is concrete.
 *
 * **Belongs in `layers/palette.ts`.** It is defined here only because this chunk does not own that
 * file; see `.wf/DRAINS-requests.md`.
 */
export const SHAFT_COLOUR: Rgba = [51, 67, 106, 225];

/**
 * `--surcharge` #EF4444 for a tide-locked outfall, reused from the palette rather than restated.
 *
 * Section 6.2 gives `--surcharge` to the surcharge markers and the reversed-flow edges, and the
 * tidal outfall is where that reversed flow enters the network - the same fact, at its source.
 * Mumbai's graph has 3 of them among 127 outfalls, so this stays rare enough to mean something.
 */
export const TIDAL_OUTFALL_COLOUR: Rgba = SURCHARGE_LINE;

// ---- Geometry ----------------------------------------------------------------------------------

/** A lon/lat/metre vertex, which is what deck's `LNGLAT` coordinate system reads as a 3D point. */
export type Position3 = [number, number, number];

const DEG = Math.PI / 180;

/**
 * Planar length of a lon/lat step at `lat`, in units where one degree of latitude is 1.
 *
 * A degree of longitude is `cos(lat)` of a degree of latitude - 0.945 at Mumbai's 19 degrees N.
 * Over one 35 m pipe that factor is nearly invisible, but it is one multiply, and a pipe that
 * turns a corner would otherwise have its bend placed a few per cent along the wrong leg.
 */
function stepLength(dLon: number, dLat: number, cosLat: number): number {
  const x = dLon * cosLat;
  return Math.hypot(x, dLat);
}

/**
 * The edge's polyline lifted to its invert profile: z interpolated linearly from `zUp` at the
 * first vertex to `zDn` at the last, by cumulative distance along the line.
 *
 * A degenerate line - one vertex, or every vertex in the same place - has no length to
 * interpolate over, so every vertex takes `zUp`. That is the honest answer for a pipe with no
 * extent, and it never divides by zero.
 */
export function pipeProfile(
  path: readonly (readonly [number, number])[],
  zUp: number,
  zDn: number,
): Position3[] {
  if (path.length === 0) return [];
  const cosLat = Math.cos((path[0][1] ?? 0) * DEG);
  const cumulative: number[] = new Array(path.length);
  cumulative[0] = 0;
  for (let i = 1; i < path.length; i += 1) {
    const [x0, y0] = path[i - 1];
    const [x1, y1] = path[i];
    cumulative[i] = cumulative[i - 1] + stepLength(x1 - x0, y1 - y0, cosLat);
  }
  const total = cumulative[path.length - 1];
  const fall = zDn - zUp;
  return path.map(([lon, lat], i) => [
    lon,
    lat,
    total > 0 ? zUp + fall * (cumulative[i] / total) : zUp,
  ]);
}

/**
 * Where an invert is drawn, given the street above it.
 *
 * Exaggeration multiplies the **cover** - how far the pipe is under its own street - and not the
 * elevation. Mumbai's inverts sit 1.5 m under the road (3.0 m on trunks), measured over
 * `city/mumbai/map/drain_nodes.geojson` on 2026-09-23, and that is genuinely shallow; the default
 * of 1 shows it. Scaling the absolute elevation instead would take a pipe at 9.58 m and draw it
 * at 14.4 m - above the street, which is the opposite of the thing being shown.
 *
 * `usedGround` is false when an exaggeration above 1 was asked for and there was no street level
 * to stretch the cover against, in which case the invert is drawn where it really is. The caller
 * counts those: an exaggeration silently applied to only part of the network would make two pipes
 * at the same depth look like different depths.
 */
export function drawnInvert(
  zInvert: number,
  zGround: number | undefined,
  exaggeration: number,
  datumOffsetM: number,
): { z: number; usedGround: boolean } {
  if (exaggeration === 1 || zGround === undefined || !Number.isFinite(zGround)) {
    return { z: zInvert + datumOffsetM, usedGround: false };
  }
  return { z: zGround - (zGround - zInvert) * exaggeration + datumOffsetM, usedGround: true };
}

// ---- Culling -----------------------------------------------------------------------------------

/**
 * Manhole shafts are drawn from this zoom in.
 *
 * At Mumbai's latitude a pixel is `156543 * cos(19.05 deg) / 2^zoom` metres: 4.52 m at zoom 15,
 * 9.03 m at zoom 14. The pipeline puts an inlet every 40 m, so at zoom 15 neighbouring shafts
 * stand about 9 px apart and read as separate objects, and at zoom 14 they are 4 px apart and
 * read as a dotted line along every road. The gate is where they stop being a texture.
 */
export const SHAFT_MIN_ZOOM = 15;

function inBbox(lon: number, lat: number, bounds: Bbox): boolean {
  const [[west, south], [east, north]] = bounds;
  return lon >= west && lon <= east && lat >= south && lat <= north;
}

function pathIntersects(path: readonly (readonly [number, number])[], bounds: Bbox): boolean {
  const [[west, south], [east, north]] = bounds;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const [x, y] of path) {
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  return maxX >= west && minX <= east && maxY >= south && minY <= north;
}

/**
 * The shafts to draw: none below {@link SHAFT_MIN_ZOOM}, none while the canvas has not been
 * measured, and otherwise the non-outfall nodes inside the view whose shaft has any height.
 *
 * Unmeasured bounds count as "draw nothing" here, where the reversed-flow layer counts them as
 * "everything is in view". The difference is the count: that layer picks from 500 stored edges,
 * this one from 49,897 nodes, and 49,897 two-point columns at an unknown camera is a frame of
 * work for something the reader may not even be looking at.
 */
export function selectShafts(
  nodes: readonly DrainNode[],
  zoom: number,
  bounds: Bbox | null,
): DrainNode[] {
  if (zoom < SHAFT_MIN_ZOOM || bounds === null) return [];
  return nodes.filter(
    (node) =>
      !node.isOutfall && node.zGroundM > node.zInvertM && inBbox(node.lon, node.lat, bounds),
  );
}

/** Every outfall, at every zoom: 127 of them in Mumbai, and 3 are the reversed-flow story. */
export function selectOutfalls(nodes: readonly DrainNode[]): DrainNode[] {
  return nodes.filter((node) => node.isOutfall);
}

/** The pipes that can be placed and are in view. An edge with no inverts is not placeable. */
export function selectPipes(drains: readonly DrainPath[], bounds: Bbox | null): DrainPath[] {
  const out: DrainPath[] = [];
  for (const edge of drains) {
    if (edge.zUpM === undefined || edge.zDnM === undefined) continue;
    if (edge.path.length < 2) continue;
    if (bounds !== null && !pathIntersects(edge.path, bounds)) continue;
    out.push(edge);
  }
  return out;
}

/** True when a pipe falls the wrong way: its downstream invert is above its upstream one. */
export function isAdverse(edge: DrainPath): boolean {
  return edge.zUpM !== undefined && edge.zDnM !== undefined && edge.zDnM > edge.zUpM;
}

// ---- Layers ------------------------------------------------------------------------------------

/** Narrowest a pipe is drawn, whatever its diameter and however far the camera is. */
export const PIPE_MIN_PIXELS = 1.5;

/** A manhole shaft's drawn width in metres: a 900 mm chamber, which is what the pipeline sizes. */
export const SHAFT_WIDTH_M = 0.9;

/** Outfall marker radius in metres, and the extra a tide-locked one gets. */
export const OUTFALL_RADIUS_M = 6;
export const TIDAL_OUTFALL_RADIUS_M = 12;

export interface Drains3dOptions {
  /** The city whose layer this is, for the message when it carries no elevations. */
  city: string;
  drains: readonly DrainPath[];
  /** The graph's nodes. Empty draws pipes only - no shafts, no outfalls, no exaggeration. */
  nodes: readonly DrainNode[];
  show: boolean;
  zoom: number;
  /** The camera's footprint in lon/lat, or null before the canvas has been measured. */
  bounds: Bbox | null;
  /**
   * How far the cover is stretched. 1, the default and the honest one, is the real depth.
   * Anything else must be labelled on screen as what it is - see {@link exaggerationLabel}.
   */
  depthExaggeration?: number;
  /** Metres added to every z, to put the DEM's frame in the ground's. See the module docstring. */
  datumOffsetM?: number;
}

export interface Drains3dReady {
  kind: "ready";
  layers: unknown[];
  pipesDrawn: number;
  /** Edges the served layer gave no inverts for, so they could not be placed. */
  pipesWithoutElevation: number;
  /** Of the pipes drawn, how many fall the wrong way (ADR-0048). */
  adverseDrawn: number;
  shaftsDrawn: number;
  outfallsDrawn: number;
  tidalOutfallsDrawn: number;
  /** True when the shafts are held back by the zoom gate or an unmeasured canvas. */
  shaftsGated: boolean;
  /** True when an exaggeration above 1 was asked for and every drawn pipe got it. */
  exaggerationApplied: boolean;
  /** Pipes an exaggeration could not be applied to because an end had no street level above it. */
  pipesWithoutGround: number;
}

/**
 * What the X-ray is doing, as something a panel can print rather than a bare layer array.
 *
 * `unavailable` is the case this whole discriminated union exists for. A drains layer exported
 * before the invert elevations were added carries none, and the only honest thing to do is say
 * so rather than fall back to z = 0. Zero is mean sea level, and Mumbai's inverts run from
 * -10.4 m to 81.0 m with a median of 9.58 m under streets whose median is 11.10 m (measured over
 * `city/mumbai/map/drain_nodes.geojson`, 2026-09-23). A zeroed network would sit 11 m under the
 * median street and 81 m under the highest ground, while the 571 genuinely sub-sea-level nodes
 * would look about right - an error invisible exactly where it is small.
 */
export type Drains3dResult =
  | { kind: "off" }
  | { kind: "loading" }
  | { kind: "unavailable"; message: string }
  | Drains3dReady;

/** One pipe with its profile already lifted, so the accessor is a field read and not a lookup. */
interface PipePath {
  id: string;
  beta: number;
  diameter: number;
  path: Position3[];
}

/** One shaft as a two-vertex vertical path from its drawn invert up to its street. */
interface ShaftPath {
  id: string;
  path: [Position3, Position3];
}

/** One outfall as a point at the pipe's mouth. */
interface OutfallPoint {
  id: string;
  position: Position3;
  tidal: boolean;
}

export function drains3dLayers(options: Drains3dOptions): Drains3dResult {
  const {
    city,
    drains,
    nodes,
    show,
    zoom,
    bounds,
    depthExaggeration = 1,
    datumOffsetM = 0,
  } = options;

  if (!show) return { kind: "off" };
  if (drains.length === 0) return { kind: "loading" };

  // Counted rather than filtered: this runs over every edge in the city and the filtered array
  // would be a second 49,770-element allocation used only for its length.
  let placeable = 0;
  for (const edge of drains) {
    if (edge.zUpM !== undefined && edge.zDnM !== undefined) placeable += 1;
  }
  if (placeable === 0) {
    return {
      kind: "unavailable",
      message:
        `This city's drain layer carries no invert elevations, so the pipes cannot be placed ` +
        `under the street. Run \`make city CITY=${city}\` to export them.`,
    };
  }

  const selected = selectPipes(drains, bounds);

  // Ground level per node, for the cover the exaggeration stretches. Built only when it is
  // needed, and only for the ends of the pipes actually being drawn. At exaggeration 1 nothing is
  // built at all. The first version was `new Map(nodes.map(...))` and it allocated all 49,897
  // entries to answer the 392 lookups a street-level view makes: that version measured a 28.4 ms
  // median at zoom 17 and 4x where this one measures 12.3-17.2 (medians of nine, same fixture,
  // 2026-09-23, 346 processes).
  let ground: Map<string, number> | null = null;
  if (depthExaggeration !== 1) {
    const needed = new Set<string>();
    for (const edge of selected) {
      if (edge.fromNode) needed.add(edge.fromNode);
      if (edge.toNode) needed.add(edge.toNode);
    }
    ground = new Map();
    for (const node of nodes) if (needed.has(node.id)) ground.set(node.id, node.zGroundM);
  }

  const pipes: PipePath[] = [];
  let endsWithoutGround = 0;
  let adverse = 0;
  for (const edge of selected) {
    const up = drawnInvert(
      edge.zUpM as number,
      ground?.get(edge.fromNode ?? ""),
      depthExaggeration,
      datumOffsetM,
    );
    const dn = drawnInvert(
      edge.zDnM as number,
      ground?.get(edge.toNode ?? ""),
      depthExaggeration,
      datumOffsetM,
    );
    if (depthExaggeration !== 1 && (!up.usedGround || !dn.usedGround)) endsWithoutGround += 1;
    pipes.push({
      id: edge.id ?? "",
      beta: edge.beta,
      diameter: edge.diameter,
      path: pipeProfile(edge.path, up.z, dn.z),
    });
    if (isAdverse(edge)) adverse += 1;
  }

  const shaftNodes = selectShafts(nodes, zoom, bounds);
  const shafts: ShaftPath[] = shaftNodes.map((node) => {
    const bottom = drawnInvert(node.zInvertM, node.zGroundM, depthExaggeration, datumOffsetM);
    return {
      id: node.id,
      path: [
        [node.lon, node.lat, bottom.z],
        [node.lon, node.lat, node.zGroundM + datumOffsetM],
      ],
    };
  });

  const outfallNodes = selectOutfalls(nodes);
  const outfalls: OutfallPoint[] = outfallNodes.map((node) => ({
    id: node.id,
    position: [
      node.lon,
      node.lat,
      drawnInvert(node.zInvertM, node.zGroundM, depthExaggeration, datumOffsetM).z,
    ],
    tidal: node.tidal,
  }));

  const layers: unknown[] = [];
  if (pipes.length > 0) {
    layers.push(
      new PathLayer<PipePath, PathStyleExtensionProps<PipePath>>({
        id: "drains-3d-pipes",
        data: pipes,
        getPath: (d) => d.path,
        getColor: (d) => drainColour(d.beta),
        // A physical object, so a physical width: a 600 mm pipe is 600 mm wide on screen and
        // gets wider as the camera comes down to it. The pixel floor is only so the network
        // stays findable from above, where a 0.45 m pipe is a fraction of a pixel.
        getWidth: (d) => d.diameter,
        widthUnits: "meters",
        widthMinPixels: PIPE_MIN_PIXELS,
        // The width faces the camera, so a pipe seen from street level reads as a tube rather
        // than vanishing edge-on the way a flat ribbon in the ground plane would.
        billboard: true,
        capRounded: true,
        jointRounded: true,
        // The same dash `layers/drains.ts` draws, for the same reason: every pipe here was
        // synthesised from roads and terrain, and a solid pipe under a photograph of a real
        // street would read as a surveyed asset. Dash lengths are multiples of the drawn width,
        // so on a 600 mm pipe this is 2.4 m of pipe and 1.8 m of gap.
        extensions: [DASHED],
        getDashArray: DRAIN_DASH,
        dashJustified: true,
        pickable: false,
      }),
    );
  }
  if (shafts.length > 0) {
    layers.push(
      new PathLayer<ShaftPath>({
        id: "drains-3d-shafts",
        data: shafts,
        getPath: (d) => d.path,
        getColor: SHAFT_COLOUR,
        getWidth: SHAFT_WIDTH_M,
        widthUnits: "meters",
        widthMinPixels: 1,
        billboard: true,
        pickable: false,
      }),
    );
  }
  if (outfalls.length > 0) {
    layers.push(
      new ScatterplotLayer<OutfallPoint>({
        id: "drains-3d-outfalls",
        data: outfalls,
        getPosition: (d) => d.position,
        getRadius: (d) => (d.tidal ? TIDAL_OUTFALL_RADIUS_M : OUTFALL_RADIUS_M),
        radiusUnits: "meters",
        radiusMinPixels: 3,
        filled: false,
        stroked: true,
        getLineColor: (d) => (d.tidal ? TIDAL_OUTFALL_COLOUR : SHAFT_COLOUR),
        lineWidthMinPixels: 1.5,
        billboard: true,
        pickable: false,
      }),
    );
  }

  return {
    kind: "ready",
    layers,
    pipesDrawn: pipes.length,
    pipesWithoutElevation: drains.length - placeable,
    adverseDrawn: adverse,
    shaftsDrawn: shafts.length,
    outfallsDrawn: outfalls.length,
    tidalOutfallsDrawn: outfalls.filter((o) => o.tidal).length,
    shaftsGated: zoom < SHAFT_MIN_ZOOM || bounds === null,
    exaggerationApplied: depthExaggeration !== 1 && endsWithoutGround === 0,
    pipesWithoutGround: endsWithoutGround,
  };
}

// ---- Memo key ----------------------------------------------------------------------------------

/**
 * A cheap identity for the camera, so a pan that changes nothing on screen rebuilds nothing.
 *
 * Building a key over the drawn data the way `useReversedFlowLayers` does would mean walking
 * 49,770 edges to decide whether to walk 49,770 edges. The camera is the only thing that changes
 * on a pan, so it is what the key is made of: bounds rounded to 1e-3 degrees (about 110 m) and
 * zoom to a half step. The integrator passes this, the two array identities and the two numeric
 * props as the memo dependencies.
 */
export function viewKey(zoom: number, bounds: Bbox | null): string {
  const z = Math.round(zoom * 2) / 2;
  if (bounds === null) return `${z}:none`;
  const r = (v: number) => Math.round(v * 1000) / 1000;
  const [[west, south], [east, north]] = bounds;
  return `${z}:${r(west)},${r(south)},${r(east)},${r(north)}`;
}

// ---- Copy --------------------------------------------------------------------------------------

/** The label on the exaggeration control: it must say what it is doing (SPEC.md 6.8). */
export function exaggerationLabel(exaggeration: number): string {
  if (exaggeration === 1) return "Real depth";
  return `Depth below the street stretched ${exaggeration}x`;
}

/**
 * The sentence under the X-ray toggle (section 6.8: sentence case, every number with its unit and
 * its context, and the honesty label is the copy rather than a footnote).
 *
 * It never claims more than was drawn: pipes the layer could not place are counted separately,
 * the shafts say why they are absent, and the uphill pipes are named so a reader who can see them
 * knows they are a property of the inferred graph and not of the renderer.
 */
export function drainXraySummary(result: Drains3dResult): string {
  if (result.kind === "off") return "The drain X-ray is off.";
  if (result.kind === "loading") return "Loading the inferred drain network.";
  if (result.kind === "unavailable") return result.message;
  const n = (value: number) => value.toLocaleString("en-IN");
  const parts: string[] = [];
  parts.push(
    `${n(result.pipesDrawn)} inferred ${result.pipesDrawn === 1 ? "pipe" : "pipes"} drawn at ` +
      `their invert depth under the street.`,
  );
  if (result.pipesWithoutElevation > 0) {
    parts.push(
      `${n(result.pipesWithoutElevation)} more carry no invert elevation and are not drawn.`,
    );
  }
  if (result.adverseDrawn > 0) {
    parts.push(
      `${n(result.adverseDrawn)} of them run uphill: the graph is inferred from roads and ` +
        `terrain and is not gravity-consistent yet.`,
    );
  }
  if (result.pipesWithoutGround > 0) {
    parts.push(
      `${n(result.pipesWithoutGround)} are drawn at their real depth because no street level ` +
        `was loaded above them, so the exaggeration could not be applied.`,
    );
  }
  if (result.shaftsGated) {
    parts.push(`Manhole shafts appear from zoom ${SHAFT_MIN_ZOOM}.`);
  } else {
    parts.push(`${n(result.shaftsDrawn)} manhole shafts in view.`);
  }
  if (result.tidalOutfallsDrawn > 0) {
    parts.push(
      `${n(result.tidalOutfallsDrawn)} of ${n(result.outfallsDrawn)} outfalls are tide-locked.`,
    );
  }
  return parts.join(" ");
}
