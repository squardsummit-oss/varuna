/**
 * Jalayantra's dispatch map: depots, the places the pumps go, the roads between them, and the
 * places left without a pump (SPEC.md 7.6), with motion M33.
 *
 * **M33 runs on deck's clock, not React's.** Each lorry leaves its depot 150 ms after the one
 * before it and drives its road in 1.2 s, the road drawing itself behind it. Nothing here
 * re-renders a component or rebuilds a layer per frame:
 *
 * - the roads are a {@link ClockTripsLayer}: a `TripsLayer` whose per-vertex timestamps are set
 *   once, and whose `currentTime` uniform is read from a {@link DispatchClock} in `draw`;
 * - everything else that changes during the dispatch - the lorry on its road, the place's disc
 *   switching from its no-pump depth to its pumped depth as the lorry arrives, the tide ring that
 *   says a pump is there - is an ordinary scatter whose instances each carry a time window, shown
 *   by the {@link DispatchWindowExtension} only while the clock is inside it. The lorry is 64
 *   points along its road, one visible at a time, so it moves once per frame at 60 fps.
 *
 * While the dispatch runs the layers ask deck to draw again next frame; when it is over they stop
 * asking, and the canvas is still (the truth pins' pattern, `truth-pins.ts`).
 *
 * Under reduced motion the clock reads "finished" from the first frame: the roads are drawn and
 * every pump is at its place, section 8's fallback, with no frame different from the last.
 *
 * Colours: the place's ring and disc are the depth ramp because they are water depth (the peak
 * with no pump, and with it); the roads and the pump marker are `--tide`, VARUNA's own action;
 * depots are `--text`, infrastructure. The depth ramp is used for nothing else (SPEC.md 6.2).
 */

import { LayerExtension, type Layer } from "@deck.gl/core";
import { TripsLayer } from "@deck.gl/geo-layers";
import { PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";

import {
  ALWAYS_AFTER,
  ALWAYS_BEFORE,
  arriveMs,
  departMs,
  type DispatchClock,
  dispatchSpanMs,
  FINISHED_MS,
  LORRY_SAMPLES,
  TRAVEL_MS,
} from "@/components/pumps/dispatch-clock";
import { depthRgba } from "@/lib/ramps";

import { ROUTE_CASING, VARUNA_ROUTE, type Rgba } from "./palette";

/** One lorry's journey, as the map draws it. */
export interface PumpRouteLeg {
  pumpId: string;
  depot: [number, number];
  target: [number, number];
  /** The place's name as the plan writes it: a register junction or an OSM street name. */
  targetName: string;
  /** The depot the lorry leaves from, named on the map while its pump is pointed at. */
  depotName: string;
  /** The truck's road, depot first; the straight line when the router gave none. */
  path: [number, number][];
  /** Whether {@link path} is a road or only the straight line the plan's ETA assumes. */
  routed: boolean;
  /** Peak depth at the place without its pump, for the ring's colour. */
  peakBeforeCm: number;
  /** Peak depth with the pump, for the disc once the pump is there. */
  peakAfterCm: number;
  /** Place in the dispatch order: this lorry leaves `order * 150 ms` after the first (M33). */
  order: number;
}

/** A place that crosses 45 cm and got no pump. */
export interface UnservedPlace {
  id: string;
  position: [number, number];
  minutesAbove: number;
}

// ---- Timing (M33) --------------------------------------------------------------------------
// The clock and its timings live in a module with no deck.gl in it, so the screen and the gauges
// can time from them without pulling WebGL into the page's first bundle.

export {
  ALWAYS_AFTER,
  ALWAYS_BEFORE,
  arriveMs,
  departMs,
  DispatchClock,
  dispatchSpanMs,
  FINISHED_MS,
  LORRY_SAMPLES,
  STAGGER_MS,
  TRAVEL_MS,
} from "@/components/pumps/dispatch-clock";

/** Cumulative length along a path, 0 at the first vertex; in degrees, which only ratios use. */
function cumulative(path: readonly [number, number][]): number[] {
  const out = [0];
  for (let i = 1; i < path.length; i += 1) {
    out.push(out[i - 1] + Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]));
  }
  return out;
}

/**
 * The time each vertex of the road is reached, in ms after the dispatch starts: the lorry leaves
 * at {@link departMs} and drives at constant speed along the road for {@link TRAVEL_MS}.
 */
export function vertexTimes(path: readonly [number, number][], order: number): number[] {
  const start = departMs(order);
  const cum = cumulative(path);
  const total = cum[cum.length - 1];
  if (!(total > 0)) return path.map((_, i) => start + (i === 0 ? 0 : TRAVEL_MS));
  return cum.map((c) => start + (c / total) * TRAVEL_MS);
}

/** The point `fraction` of the way along a path by length. */
export function pointAlong(path: readonly [number, number][], fraction: number): [number, number] {
  if (path.length === 0) return [0, 0];
  if (path.length === 1 || fraction <= 0) return path[0];
  if (fraction >= 1) return path[path.length - 1];
  const cum = cumulative(path);
  const target = cum[cum.length - 1] * fraction;
  for (let i = 1; i < path.length; i += 1) {
    if (cum[i] >= target) {
      const span = cum[i] - cum[i - 1];
      const t = span > 0 ? (target - cum[i - 1]) / span : 0;
      return [
        path[i - 1][0] + (path[i][0] - path[i - 1][0]) * t,
        path[i - 1][1] + (path[i][1] - path[i - 1][1]) * t,
      ];
    }
  }
  return path[path.length - 1];
}

/** One drawn position of a lorry, shown while the clock is inside `window` ([from, to) ms). */
export interface LorrySample {
  pumpId: string;
  position: [number, number];
  window: [number, number];
}

/**
 * A lorry as {@link LORRY_SAMPLES} points along its road, each shown for its slice of the drive.
 * The first is shown from before the dispatch (the lorry waits at its depot for its turn); the
 * last slice ends at arrival, when the pump ring at the place takes over.
 */
export function lorrySamples(leg: PumpRouteLeg, samples = LORRY_SAMPLES): LorrySample[] {
  const n = Math.max(2, samples);
  const start = departMs(leg.order);
  const step = TRAVEL_MS / (n - 1);
  const out: LorrySample[] = [];
  for (let j = 0; j < n - 1; j += 1) {
    out.push({
      pumpId: leg.pumpId,
      position: pointAlong(leg.path, j / (n - 1)),
      window: [j === 0 ? ALWAYS_BEFORE : start + j * step, start + (j + 1) * step],
    });
  }
  return out;
}

// ---- The clock on the GPU ------------------------------------------------------------------

interface ClockProps {
  /** The dispatch's clock; null draws the finished state. */
  clock: DispatchClock | null;
  /** How long this dispatch runs, so the layer knows when to stop asking for frames. */
  clockSpanMs: number;
}

/** What the clock reads for a layer, and whether it should ask deck for another frame. */
export function clockFrame(
  clock: DispatchClock | null,
  spanMs: number,
  now: number = performance.now(),
): { elapsed: number; running: boolean } {
  const elapsed = clock ? clock.elapsed(now) : FINISHED_MS;
  return { elapsed, running: elapsed >= 0 && elapsed < spanMs };
}

interface TripDatum {
  path: [number, number][];
  times: number[];
}

/**
 * A `TripsLayer` whose `currentTime` is the dispatch clock, read in `draw`: the road is drawn up
 * to where the lorry is, with no trail fade, and a finished dispatch is the whole road.
 */
export class ClockTripsLayer<D extends TripDatum = TripDatum> extends TripsLayer<D, ClockProps> {
  static layerName = "ClockTripsLayer";
  static defaultProps = {
    clock: { type: "object", value: null, compare: false },
    clockSpanMs: 0,
    fadeTrail: false,
  };

  draw(params: unknown): void {
    const { elapsed, running } = clockFrame(this.props.clock, this.props.clockSpanMs);
    const model = (this.state as { model?: { shaderInputs: { setProps: (p: object) => void } } })
      .model;
    model?.shaderInputs.setProps({
      trips: { fadeTrail: false, trailLength: 0, currentTime: elapsed },
    });
    // PathLayer's own draw; TripsLayer's would overwrite `currentTime` from props.
    PathLayer.prototype.draw.call(this, params as { uniforms: unknown });
    if (running) this.setNeedsRedraw();
  }
}

/** The shader module: one uniform, the clock, and each instance's window as an attribute. */
export const WINDOW_SHADER = {
  name: "dispatchWindow",
  vs: /* glsl */ `\
layout(std140) uniform dispatchWindowUniforms {
  float elapsed;
} dispatchWindow;
in vec2 instanceDispatchWindow;
`,
  uniformTypes: { elapsed: "f32" },
  inject: {
    // Outside its window an instance collapses to a point off the clip volume: no fragments.
    "vs:#main-end": `\
  if (dispatchWindow.elapsed < instanceDispatchWindow.x || dispatchWindow.elapsed >= instanceDispatchWindow.y) {
    gl_Position = vec4(0.0);
  }
`,
  },
} as const;

type WindowProps = ClockProps & {
  getDispatchWindow: (d: unknown) => [number, number];
};

/** Shows each instance only while the dispatch clock is inside its window. */
export class DispatchWindowExtension extends LayerExtension {
  static extensionName = "DispatchWindowExtension";
  static defaultProps = {
    getDispatchWindow: { type: "accessor", value: [ALWAYS_BEFORE, ALWAYS_AFTER] },
    clock: { type: "object", value: null, compare: false },
    clockSpanMs: 0,
  };

  getShaders() {
    return { modules: [WINDOW_SHADER] };
  }

  initializeState(this: Layer<WindowProps>) {
    this.getAttributeManager()?.addInstanced({
      instanceDispatchWindow: { size: 2, accessor: "getDispatchWindow" },
    });
  }

  draw(this: Layer<WindowProps>) {
    const { elapsed, running } = clockFrame(this.props.clock, this.props.clockSpanMs);
    this.setShaderModuleProps({ dispatchWindow: { elapsed } });
    if (running) this.setNeedsRedraw();
  }
}

/** One instance for every layer, so a rebuilt layer keeps its compiled program. */
const WINDOW = new DispatchWindowExtension();
/** Depots closer than this share one label: at the plan's framing their names would overprint. */
const DEPOT_LABEL_MERGE_M = 900;
/**
 * Places closer than this share one label: they are one place on the map. Danda Avenue and Danda
 * Boulevard, where the 08:40 plan sends P-10 and P-03, are 17 m apart.
 */
const PLACE_LABEL_MERGE_M = 250;
/**
 * Metres a screen pixel covers at the plan's own framing: the 08:40 plan's 13.6 km north to
 * south in the 450 px a 1440 x 900 map leaves it, measured 2026-09-28. Used only to guess how
 * far a name reaches, so a label can be put on the side where it covers no other ring.
 */
const PLAN_M_PER_PX = 35;
/** A 12 px semibold name: about 6.5 px a character, after the 13 px gap from its ring. */
const LABEL_GAP_PX = 13;
const LABEL_CHAR_PX = 6.5;
/** A ring within this many metres north or south sits on the label's line. */
const LABEL_LINE_M = 500;

/** One label for a group of nearby points: their names stacked, at the first point. */
export interface DepotLabel {
  text: string;
  position: [number, number];
}

/** Metres between two lon/lat points, on a local flat Earth: plenty for label spacing. */
function metresBetween(a: [number, number], b: [number, number]): number {
  const kx = 111_320 * Math.cos(((a[1] + b[1]) / 2) * (Math.PI / 180));
  return Math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 110_540);
}

/**
 * Names grouped so none overprints another: a point within `mergeM` of a group's first point
 * joins it, and the group reads as one stacked label. Greedy in the order given, so the grouping
 * is deterministic. Every point's marker is still drawn on its own.
 */
export function stackLabels(
  points: readonly { name: string; position: [number, number] }[],
  mergeM: number,
): DepotLabel[] {
  const groups: { position: [number, number]; names: string[] }[] = [];
  for (const point of points) {
    if (!point.name) continue;
    const near = groups.find((g) => metresBetween(g.position, point.position) < mergeM);
    if (near) {
      if (!near.names.includes(point.name)) near.names.push(point.name);
    } else groups.push({ position: point.position, names: [point.name] });
  }
  return groups.map((g) => ({ text: g.names.join("\n"), position: g.position }));
}

/**
 * Depot names grouped so none overprints another. Kurla's BEST depot and the L ward office sit
 * 540 m apart, and at the plan's framing their names ran into one unreadable line; merged, they
 * read as two lines under one dot.
 */
export function depotLabels(
  depots: readonly { name: string; position: [number, number] }[],
  mergeM = DEPOT_LABEL_MERGE_M,
): DepotLabel[] {
  return stackLabels(depots, mergeM);
}

/** A place's name, on the side of its ring where it covers no other place. */
export interface PlaceLabel extends DepotLabel {
  anchor: "start" | "end";
}

/**
 * The name of every place a pump goes, drawn by this layer at every zoom. The shared label layer
 * starts hotspot names at zoom 11.5, and the plan's own framing opens near zoom 11, so without
 * these the map showed twelve rings and no word of where they were.
 *
 * A name reads to the right of its ring unless another ring sits on that line within the name's
 * reach, then to the left: on the 08:40 plan "Lokmanya Tilak Nagar" ran into the sangharsh nagar
 * road ring 1.2 km east of it, and "Jijamata Road" into Road 13's.
 */
export function placeLabels(
  legs: readonly PumpRouteLeg[],
  mergeM = PLACE_LABEL_MERGE_M,
  metresPerPx = PLAN_M_PER_PX,
): PlaceLabel[] {
  const groups = stackLabels(
    legs.map((leg) => ({ name: leg.targetName, position: leg.target })),
    mergeM,
  );
  const rings = legs.map((leg) => leg.target);
  return groups.map((group) => {
    const [lon, lat] = group.position;
    const kx = 111_320 * Math.cos(lat * (Math.PI / 180));
    const longest = Math.max(...group.text.split("\n").map((line) => line.length));
    const reachM = (LABEL_GAP_PX + LABEL_CHAR_PX * longest) * metresPerPx;
    const blocked = (side: 1 | -1) =>
      rings.some((ring) => {
        const dx = (ring[0] - lon) * kx * side;
        const dy = Math.abs(ring[1] - lat) * 110_540;
        return dx > mergeM && dx <= reachM && dy < LABEL_LINE_M;
      });
    const anchor = blocked(1) && !blocked(-1) ? "end" : "start";
    return { ...group, anchor };
  });
}

/**
 * The page's own UI face, resolved for a canvas. A canvas cannot read `var(--font-sans)`, and an
 * unreadable font string is dropped whole, which leaves the atlas in the browser's default serif.
 */
let resolvedFont: string | null = null;
function canvasFont(): string {
  if (resolvedFont) return resolvedFont;
  const fallback = "system-ui, sans-serif";
  if (typeof document === "undefined" || !document.body) return fallback;
  const family = getComputedStyle(document.body).fontFamily;
  resolvedFont = family && !family.includes("var(") ? family : fallback;
  return resolvedFont;
}

// ---- Layers --------------------------------------------------------------------------------

export interface PumpRouteLayerOptions {
  legs: readonly PumpRouteLeg[];
  depots: readonly { name: string; position: [number, number] }[];
  unserved?: readonly UnservedPlace[];
  /** The leg the operator is pointing at, drawn brighter; others dim a little. */
  selectedPumpId?: string | null;
  /** The dispatch being drawn (M33); null draws it finished. */
  clock?: DispatchClock | null;
}

/** `--text` #E3EAF6: a depot is infrastructure, not water, so it is drawn in the text colour. */
const DEPOT_FILL: Rgba = [227, 234, 246, 235];
/** `--text-2` #A7B4CC: the depot's name, quieter than the places the pumps go. */
const DEPOT_LABEL: Rgba = [167, 180, 204, 255];
/** `--text` #E3EAF6: the name of a place a pump goes, the loudest words on the map. */
const PLACE_LABEL: Rgba = [227, 234, 246, 255];
/** `--ink` #0A1020: the rim that separates a marker from the imagery under it. */
const INK: Rgba = [10, 16, 32, 255];
/** `--tide` at 45 %: the straight line the plan assumed where the router found no road. */
const STRAIGHT_LINE: Rgba = [45, 212, 191, 115];
/** `--tide` at 40 %: a road that is not the one being pointed at. */
const DIMMED_ROUTE: Rgba = [45, 212, 191, 102];
/** Unserved places are drawn at the first depth they are known to exceed: 45 cm. */
const UNSERVED_DEPTH_CM = 45.5;

/** What the layers draw from a set of legs, computed once per set: hovering a row rebuilds the
 * layers with a new colour, and must not re-time 12 roads and 756 lorry positions to do it. */
const PREPARED = new WeakMap<
  readonly PumpRouteLeg[],
  {
    span: number;
    trips: (PumpRouteLeg & TripDatum)[];
    roads: (PumpRouteLeg & TripDatum)[];
    lorries: LorrySample[];
    places: PlaceLabel[];
  }
>();

function prepared(legs: readonly PumpRouteLeg[]) {
  let hit = PREPARED.get(legs);
  if (!hit) {
    hit = {
      span: dispatchSpanMs(legs.length === 0 ? 0 : Math.max(...legs.map((l) => l.order)) + 1),
      trips: legs.map((leg) => ({ ...leg, times: vertexTimes(leg.path, leg.order) })),
      roads: [],
      lorries: legs.flatMap((leg) => lorrySamples(leg)),
      places: placeLabels(legs),
    };
    hit.roads = hit.trips.filter((d) => d.routed);
    PREPARED.set(legs, hit);
  }
  return hit;
}

/** Every layer of the dispatch map, bottom to top. */
export function pumpRouteLayers({
  legs,
  depots,
  unserved = [],
  selectedPumpId = null,
  clock = null,
}: PumpRouteLayerOptions): unknown[] {
  const { span, trips, roads, lorries, places } = prepared(legs);
  // Depot names only for the pump being pointed at: twelve grey names over the roads buried the
  // places the pumps go, and the list beside the map names every depot already. The white dots
  // and the key say what a depot is.
  const labels = selectedPumpId
    ? depotLabels(
        legs
          .filter((leg) => leg.pumpId === selectedPumpId && leg.depotName)
          .map((leg) => ({ name: leg.depotName, position: leg.depot })),
      )
    : [];
  const font = canvasFont();
  const colourOf = (leg: PumpRouteLeg): Rgba => {
    if (!leg.routed) return STRAIGHT_LINE;
    if (selectedPumpId && leg.pumpId !== selectedPumpId) return DIMMED_ROUTE;
    return VARUNA_ROUTE;
  };
  const clocked = { clock, clockSpanMs: span };

  return [
    new ScatterplotLayer<UnservedPlace>({
      id: "pumps-unserved",
      data: unserved as UnservedPlace[],
      getPosition: (d) => d.position,
      getRadius: 60,
      radiusUnits: "meters",
      radiusMinPixels: 3,
      filled: false,
      stroked: true,
      getLineColor: depthRgba(UNSERVED_DEPTH_CM, 0.55),
      lineWidthMinPixels: 1.5,
    }),
    new ClockTripsLayer({
      id: "pumps-road-casing",
      data: roads,
      getPath: (d) => d.path,
      getTimestamps: (d) => d.times,
      getColor: ROUTE_CASING,
      getWidth: 7,
      widthUnits: "pixels",
      capRounded: true,
      jointRounded: true,
      ...clocked,
    }),
    new ClockTripsLayer({
      id: "pumps-road",
      data: trips,
      getPath: (d) => d.path,
      getTimestamps: (d) => d.times,
      getColor: (d) => colourOf(d as unknown as PumpRouteLeg),
      getWidth: (d) => ((d as unknown as PumpRouteLeg).routed ? 4 : 2),
      widthUnits: "pixels",
      capRounded: true,
      jointRounded: true,
      updateTriggers: { getColor: selectedPumpId },
      ...clocked,
    }),
    new ScatterplotLayer<{ name: string; position: [number, number] }>({
      id: "pumps-depot",
      data: depots as { name: string; position: [number, number] }[],
      getPosition: (d) => d.position,
      getRadius: 90,
      radiusUnits: "meters",
      radiusMinPixels: 4,
      getFillColor: DEPOT_FILL,
      stroked: true,
      getLineColor: INK,
      lineWidthMinPixels: 1.5,
    }),
    new TextLayer<DepotLabel>({
      id: "pumps-depot-label",
      data: labels,
      getPosition: (d) => d.position,
      getText: (d) => d.text,
      getSize: 11,
      getColor: DEPOT_LABEL,
      getPixelOffset: [0, 12],
      getTextAnchor: "middle",
      getAlignmentBaseline: "top",
      lineHeight: 1.25,
      fontFamily: font,
      outlineWidth: 3,
      outlineColor: INK,
      fontSettings: { sdf: true, fontSize: 64, buffer: 8 },
      characterSet: "auto",
    }),
    // The place: a ring in the depth ramp at its peak without the pump. Inside it, the disc is
    // the same no-pump depth until the lorry arrives, then its peak with the pump - the change
    // the gauge beside the map drains through (M34) - and a tide ring says the pump is there.
    new ScatterplotLayer<PumpRouteLeg>({
      id: "pumps-place-ring",
      data: legs as PumpRouteLeg[],
      getPosition: (d) => d.target,
      getRadius: 140,
      radiusUnits: "meters",
      radiusMinPixels: 7,
      filled: true,
      getFillColor: INK,
      stroked: true,
      getLineColor: (d) => depthRgba(d.peakBeforeCm),
      getLineWidth: (d) => (d.pumpId === selectedPumpId ? 4 : 3),
      lineWidthUnits: "pixels",
      updateTriggers: { getLineWidth: selectedPumpId },
    }),
    new ScatterplotLayer<PumpRouteLeg>({
      id: "pumps-place-core-before",
      data: legs as PumpRouteLeg[],
      getPosition: (d) => d.target,
      getRadius: 70,
      radiusUnits: "meters",
      radiusMinPixels: 3.5,
      getFillColor: (d) => depthRgba(d.peakBeforeCm),
      extensions: [WINDOW],
      getDispatchWindow: (d: PumpRouteLeg) => [ALWAYS_BEFORE, arriveMs(d.order)],
      ...clocked,
    } as ConstructorParameters<typeof ScatterplotLayer<PumpRouteLeg>>[0]),
    new ScatterplotLayer<PumpRouteLeg>({
      id: "pumps-place-core-after",
      data: legs as PumpRouteLeg[],
      getPosition: (d) => d.target,
      getRadius: 70,
      radiusUnits: "meters",
      radiusMinPixels: 3.5,
      getFillColor: (d) => depthRgba(d.peakAfterCm),
      extensions: [WINDOW],
      getDispatchWindow: (d: PumpRouteLeg) => [arriveMs(d.order), ALWAYS_AFTER],
      ...clocked,
    } as ConstructorParameters<typeof ScatterplotLayer<PumpRouteLeg>>[0]),
    new ScatterplotLayer<PumpRouteLeg>({
      id: "pumps-pump-here",
      data: legs as PumpRouteLeg[],
      getPosition: (d) => d.target,
      getRadius: 200,
      radiusUnits: "meters",
      radiusMinPixels: 10,
      filled: false,
      stroked: true,
      getLineColor: VARUNA_ROUTE,
      lineWidthMinPixels: 2,
      extensions: [WINDOW],
      getDispatchWindow: (d: PumpRouteLeg) => [arriveMs(d.order), ALWAYS_AFTER],
      ...clocked,
    } as ConstructorParameters<typeof ScatterplotLayer<PumpRouteLeg>>[0]),
    new ScatterplotLayer<LorrySample>({
      id: "pumps-lorry",
      data: lorries,
      getPosition: (d) => d.position,
      getRadius: 6,
      radiusUnits: "pixels",
      getFillColor: VARUNA_ROUTE,
      stroked: true,
      getLineColor: INK,
      lineWidthMinPixels: 2,
      extensions: [WINDOW],
      getDispatchWindow: (d: LorrySample) => d.window,
      ...clocked,
    } as ConstructorParameters<typeof ScatterplotLayer<LorrySample>>[0]),
    // Where each pump goes, by name, beside its ring and over every road: the map's answer to
    // "where", readable at the plan's own framing.
    new TextLayer<PlaceLabel>({
      id: "pumps-place-label",
      data: places,
      getPosition: (d) => d.position,
      getText: (d) => d.text,
      getSize: 12,
      getColor: PLACE_LABEL,
      getPixelOffset: (d) => [d.anchor === "end" ? -LABEL_GAP_PX : LABEL_GAP_PX, 0],
      getTextAnchor: (d) => d.anchor,
      getAlignmentBaseline: "center",
      lineHeight: 1.2,
      fontFamily: font,
      fontWeight: 600,
      outlineWidth: 3,
      outlineColor: INK,
      fontSettings: { sdf: true, fontSize: 64, buffer: 8 },
      characterSet: "auto",
    }),
  ];
}
