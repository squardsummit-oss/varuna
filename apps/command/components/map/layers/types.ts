/**
 * The data `CityMap` draws, one interface per thing on the map (SPEC.md 6.7).
 *
 * These lived at the top of `city-map.tsx` until MO1 split it into one module per layer.
 * `city-map.tsx` re-exports every one of them, so no importer had to change.
 */

/** One segment's geometry plus the depth series the run gave it. */
export interface SegmentPath {
  id: string;
  path: [number, number][];
  /** Depth in cm at each of the run's steps; empty means the run never wet it. */
  depthCm: number[];
  /** P(depth > threshold) per step, keyed by the threshold in cm ("30"), measured across the
   * run's members. Absent on a run with no spread, where probability mode compares the depth. */
  pGt?: Record<string, number[]>;
  /** Road class, which sets the drawn width (section 6.7: 2-6 px by class). */
  width: number;
  /** Street name from OSM, where it has one. Drawn as a label at high zoom. */
  name?: string;
  /** The name a tooltip or list prints: OSM's, else "off <street>" or "<class> near <place>"
   * from the API's `display_name`. Never drawn as a map label - only OSM's name is. */
  displayName?: string;
  /** Change in peak depth under a what-if, in cm. Negative is an improvement. */
  deltaCm?: number;
}

export interface SurchargeNode {
  id: string;
  lon: number;
  lat: number;
  /** Discharge out of the manhole at the current step, in m³/s; sets the marker's size. */
  q?: number;
}

export interface HotspotRing {
  id: string;
  name: string;
  lon: number;
  lat: number;
}

/** A building footprint ring, in lon/lat. */
export type BuildingPolygon = [number, number][];

/** One inferred drain edge, coloured by its blockage prior. */
export interface DrainPath {
  /** Edge id, where the caller has one. Optional because `layers/drains.ts` never needed it and
   * the layers are fed from more than one place; the API client's own `DrainPath` always sets it. */
  id?: string;
  path: [number, number][];
  /** Blockage 0-1; the magenta ramp of section 6.2. */
  beta: number;
  /** Pipe diameter in metres, which sets the drawn width (section 6.7: 1-4 px). */
  diameter: number;
  /**
   * Invert elevation in metres at the edge's from-node and to-node, in the DEM's own vertical
   * frame. What the X-ray (`layers/drains-3d.ts`) places the pipe at.
   *
   * Optional, and that is not defensive coding: the map layer's property allow-list
   * (`services/city/varuna_city/export.py`, `MAP_KEEP_COLUMNS`) did not carry these until the
   * elevations were added to it, so a deployed API serving a city exported before that change
   * answers with neither. The X-ray reports that rather than drawing the sewer at zero.
   */
  zUpM?: number;
  zDnM?: number;
  /** The node ids at each end, so the nodes layer's ground elevations can be joined on. */
  fromNode?: string;
  toNode?: string;
}

/**
 * One node of the inferred drain graph: a manhole, an inlet, a depression bottom, a trunk
 * junction or an outfall.
 *
 * `kind` is the string the city pipeline writes. Mumbai's export has exactly five values,
 * counted from `city/mumbai/map/drain_nodes.geojson` on 2026-09-23: inlet 46,247,
 * depression 2,448, trunk 1,047, outfall 127, hotspot 28. It is typed as a string rather than
 * a union so a city built with a newer pipeline does not fail to parse over a sixth kind.
 */
export interface DrainNode {
  id: string;
  lon: number;
  lat: number;
  kind: string;
  /** Street level above the node, metres. The top of its shaft. */
  zGroundM: number;
  /** Pipe invert at the node, metres. The bottom of its shaft. */
  zInvertM: number;
  /** True where the network discharges to a waterway or the sea. */
  isOutfall: boolean;
  /** True for an outfall whose stage is the tide: where the reversed flow comes in. */
  tidal: boolean;
}

/** One drawn route: the naive shortest path, the VARUNA route, or an alternate. */
export interface RouteLine {
  id: string;
  path: [number, number][];
  /** `naive` is the dashed grey comparison; `varuna` the tide-coloured route (section 6.7);
   * `avoided` is a street the route refused, drawn in the depth ramp's deepest red. */
  kind: "naive" | "varuna" | "alternate" | "avoided";
}

/** One reachability band, drawn as a translucent polygon (section 6.2 `--reach-*`). */
export interface Isochrone {
  minutes: number;
  rings: [number, number][][];
}

/** A street the operator pointed at: the segment, and where on screen to anchor a panel. */
export interface SegmentPick {
  segment: SegmentPath;
  x: number;
  y: number;
}

/** One sourced ground-truth pin, drawn where a civic log said the water was (task P6.12). */
export interface TruthPin {
  id: string;
  lon: number;
  lat: number;
  name: string;
  /**
   * `performance.now()` at which this pin began to drop (motion M18). Absent for a pin that has
   * landed, or that was already passed when the clock was first read; `Infinity` for a pin the
   * clock has just passed whose first frame has not arrived yet, which draws nothing.
   */
  dropStartMs?: number;
}

/** Where to fly. `key` changes on every request, so clicking the same row twice flies again. */
export interface MapFocus {
  lon: number;
  lat: number;
  key: string;
  /** Zoom to settle at; the flight never zooms out from a closer view the operator chose. */
  zoom?: number;
}

// ---- Seams for the motion and Pulse chunks ------------------------------------------------------
// Typed now so the chunks that fill them edit only `layers/*`. Nothing below is drawn yet; each
// says which chunk draws it.

/**
 * How far each route kind has drawn, 0 to 1 (motion M14). Today only `varuna` animates and
 * `naive` is always 1; MO8 gives the naive route its own window and the avoided streets a flash.
 */
export interface RouteProgress {
  naive: number;
  varuna: number;
  /** Alpha 0-255 of the avoided streets. Absent draws them at their palette alpha. Set by MO8. */
  avoidedAlpha?: number;
}

/** A drain edge flowing backwards at the current step (motion M9). Drawn by MO3, not yet. */
export interface ReversedEdgePath {
  edgeId: string;
  /** `path[0]` is the edge's from-node; reversed flow runs toward it. */
  path: [number, number][];
  /** Most negative discharge over the run, m³/s; sets the drawn width. */
  minQ: number;
  /** True for the tide-locked outfall edge, which is always kept. */
  tidal: boolean;
}

/** A drain inlet and its clogging κ (SPEC.md 7.3's small squares). Drawn by PU8, not yet. */
export interface InletPoint {
  lon: number;
  lat: number;
  kappa: number;
  /** True when Pulse has updated this inlet; prior-only inlets draw fainter. */
  learned: boolean;
}

/** A pipe under the cursor on `/drains` (PU8). */
export interface DrainPick {
  drain: DrainPath;
  x: number;
  y: number;
}

/** How surcharging manholes are drawn: the console's pulse, or `/drains`' static ring (PU8). */
export type SurchargeStyle = "pulse" | "ring";
